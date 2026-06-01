from contextlib import asynccontextmanager
from uuid import uuid4
import asyncio

import json
import events
from fastapi import Request
from fastapi.responses import StreamingResponse
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

import database
import orchestrator
from models import RunStatus
from src.agent.derive_profile import derive_domain_profile
from util import utcnow_iso
from model_manager import ModelManager
from src.comfy.client import ComfyUIClient


class DefectsPerImage(BaseModel):
    min: int = 1
    max: int = 3


class Gates(BaseModel):
    mask_review: bool = True
    prompt_review: bool = False
    candidate_review: bool = True


class ScoreWeights(BaseModel):
    diff: float = 0.4
    vision: float = 0.6


class Thresholds(BaseModel):
    auto_accept_above: float = 0.80
    auto_reject_below: float = 0.45


class RunConfig(BaseModel):
    source_image_ids: list[str]
    dataset_description: str
    defect_taxonomy: list[str] = ["scratch", "crack"]#Field(min_length=1)
    eval_prompt: str
    pilot_count: int = 1 #5
    target_count: int = 10 #30
    gates: Gates = Field(default_factory=Gates)
    score_weights: ScoreWeights = Field(default_factory=ScoreWeights)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    on_reject: str = "retry_same_region"
    defects_per_image: DefectsPerImage = Field(default_factory=DefectsPerImage)


class ApproveMaskRequest(BaseModel):
    region_ids: list[str] | None = None     # None = keep all detected regions


class DecisionRequest(BaseModel):
    decision: Literal["accept", "reject"]
    reason: str = ""
    

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = await database.connect()
    await database.init_db(app.state.db)
    app.state.mm = ModelManager()
    app.state.comfy = ComfyUIClient()
    app.state.tasks = {}

    for run in await database.list_runs(app.state.db):     # restart-resume
        run = await orchestrator.sync_run(app.state.db, run)
        if run["status"] in orchestrator.LIVE_RUN:
            kick_loop(run["id"])

    yield

    for task in app.state.tasks.values():
        task.cancel()
    await app.state.db.close()


async def _run_loop(run_id: str):
    db = app.state.db
    try:
        while True:
            run = await database.get_run(db, run_id)
            if run is None:
                break
            if not await orchestrator.advance(db, run):
                break
            await asyncio.sleep(orchestrator.LOOP_DELAY)
    finally:
        app.state.tasks.pop(run_id, None)


def kick_loop(run_id: str):
    task = app.state.tasks.get(run_id)
    if task and not task.done():
        return                                 # already running — don't double-spawn
    app.state.tasks[run_id] = asyncio.create_task(_run_loop(run_id))


app = FastAPI(title="Nuwa", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "nuwa"}


@app.get("/runs")
async def list_runs():
    return await database.list_runs(app.state.db)


@app.post("/runs", status_code=201)
async def create_run(config: RunConfig):
    config_dict = config.model_dump()
    profile = derive_domain_profile(config_dict)
    now = utcnow_iso()
    run = {
        "id": "run_" + uuid4().hex[:8],
        "status": RunStatus.DRAFT.value,
        "config": config_dict,
        "domain_profile": profile,
        "progress": {
            "phase": "pilot",
            "target": config.target_count,
            "pilot_target": config.pilot_count,
            "accepted": 0,
            "rejected": 0,
            "mask_approved": False,
            "generated": 0,
            "per_class": {d: 0 for d in config.defect_taxonomy},
        },
        "regions": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }
    await database.insert_run(app.state.db, run)
    return run


@app.get("/runs/{run_id}")
async def get_run(run_id: str):
    run = await database.get_run(app.state.db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@app.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request):
    run = await database.get_run(app.state.db, run_id)
    if run is None:
        raise HTTPException(404, "run not found")

    async def stream():
        q = events.bus.subscribe(run_id)
        try:
            cands = await database.list_candidates(app.state.db, run_id)
            yield f"data: {json.dumps(orchestrator._snapshot(run, cands))}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"          # heartbeat — keeps proxies from closing idle conns
        finally:
            events.bus.unsubscribe(run_id, q)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",            # disable nginx buffering if you proxy in prod
    })


@app.post("/runs/{run_id}/detect")
async def detect(run_id: str):
    run = await database.get_run(app.state.db, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if run["status"] != RunStatus.DRAFT.value:
        raise HTTPException(409, f"can only detect from draft (is {run['status']})")
    run["regions"] = orchestrator._mock_detect_regions(run)   # Track 3: real SAM3, one-shot
    run = await orchestrator.sync_run(app.state.db, run)
    return {"run_status": run["status"], "regions": run["regions"]}


@app.post("/runs/{run_id}/approve-mask")
async def approve_mask(run_id: str, body: ApproveMaskRequest):
    run = await database.get_run(app.state.db, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if run["status"] != RunStatus.AWAITING_MASK_REVIEW.value:
        raise HTTPException(409, f"not awaiting mask review (is {run['status']})")
    if body.region_ids is not None:                           # prune to the kept subset
        keep = set(body.region_ids)
        run["regions"] = [r for r in run["regions"] if r["id"] in keep]
        if not run["regions"]:
            raise HTTPException(400, "must keep at least one region")
    run["progress"]["mask_approved"] = True
    run = await orchestrator.sync_run(app.state.db, run)
    if run["status"] in orchestrator.LIVE_RUN:
        kick_loop(run_id)
    return {"run_status": run["status"], "region_count": len(run["regions"])}


@app.post("/candidates/{cid}/decision")
async def decide(cid: str, body: DecisionRequest):
    cand = await database.get_candidate(app.state.db, cid)
    if cand is None or cand["status"] != orchestrator.CS.AWAITING_REVIEW.value:
        raise HTTPException(409, "candidate not awaiting review")
    if body.decision == "reject":
        if not body.reason.strip():
            raise HTTPException(422, "a rejection must include a reason")
        reason = body.reason.strip()
    else:
        reason = None        # accepts carry no teaching signal — drop any reason sent
    run = await database.get_run(app.state.db, cand["run_id"])
    await orchestrator.apply_decision(app.state.db, run, cand, body.decision,
                                      by="human", reason=reason)
    run = await orchestrator.sync_run(app.state.db, run)
    if run["status"] in orchestrator.LIVE_RUN:
        kick_loop(run["id"])
    return {"candidate_status": cand["status"], "run_status": run["status"],
            "progress": run["progress"]}


@app.post("/runs/{run_id}/abort")
async def abort_run(run_id: str):
    run = await database.get_run(app.state.db, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if run["status"] in orchestrator.TERMINAL_RUN:
        raise HTTPException(409, f"run already finished ({run['status']})")
    task = app.state.tasks.pop(run_id, None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    run["status"] = RunStatus.ABORTED.value
    await orchestrator.sync_run(app.state.db, run)
    return {"run_status": run["status"]}


@app.get("/runs/{run_id}/candidates")
async def get_candidates(run_id: str):
    return await database.list_candidates(app.state.db, run_id)


# --- Debug endpoints below


@app.post("/debug/candidates/{cid}/step")
async def debug_step_candidate(cid: str):
    cand = await database.get_candidate(app.state.db, cid)
    if cand is None:
        raise HTTPException(404, "candidate not found")
    run  = await database.get_run(app.state.db, cand["run_id"])
    cand = await orchestrator.step_candidate(app.state.db, run, cand)
    run  = await orchestrator.sync_run(app.state.db, run)
    return {"candidate_status": cand["status"], "run_status": run["status"],
            "progress": run["progress"]}

