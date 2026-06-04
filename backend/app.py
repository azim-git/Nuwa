from contextlib import asynccontextmanager
from uuid import uuid4
import asyncio
from dotenv import load_dotenv
load_dotenv()  # load backend/.env before anything reads os.environ

import json
from src.sam3.client import Sam3Client
import events
from fastapi import Request
from fastapi.responses import StreamingResponse
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

import export as export_mod
import database
import orchestrator
from models import RunStatus
from src.agent.derive_profile import derive_domain_profile
from util import utcnow_iso
from model_manager import ModelManager
from src.comfy.client import ComfyUIClient
from src.vision.client import VisionClient
from src.agent.client import AgentClient

from fastapi import UploadFile, File
from fastapi.responses import FileResponse
from PIL import Image
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)-20s %(levelname)s  %(message)s")


class DefectsPerImage(BaseModel):
    min: int = 1
    max: int = 10


class Gates(BaseModel):
    mask_review: bool = True
    prompt_review: bool = False
    candidate_review: bool = True


class ScoreWeights(BaseModel):
    diff: float = 0.4
    vision: float = 0.6


class Thresholds(BaseModel):
    auto_accept_above: float = 0.50
    auto_reject_below: float = 0.45


class RunConfig(BaseModel):
    source_image_ids: list[str]
    dataset_description: str
    defect_taxonomy: list[str] = Field(min_length=1) # ["scratch", "crack"]
    eval_prompt: str
    placement: str = ""
    pilot_count: int = 2 #5
    target_count: int = 5 #30
    gates: Gates = Field(default_factory=Gates)
    score_weights: ScoreWeights = Field(default_factory=ScoreWeights)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    on_reject: str = "retry_same_region"
    defects_per_image: DefectsPerImage = Field(default_factory=DefectsPerImage)


class ApproveMaskRequest(BaseModel):
    region_ids:       list[str] | None = None   # None = keep all
    disabled_defects: list[str] | None = None   # defects toggled off at mask review


class DecisionRequest(BaseModel):
    decision: Literal["accept", "reject"]
    reason: str = ""
    

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = await database.connect()
    await database.init_db(app.state.db)
    app.state.mm = ModelManager()

    app.state.sam3 = Sam3Client()
    app.state.mm.register("sam3", app.state.sam3.unload)

    app.state.comfy = ComfyUIClient()
    app.state.mm.register("comfyui", app.state.comfy.unload)

    app.state.vision = VisionClient()
    app.state.mm.register("vision", app.state.vision.unload)

    app.state.agent = AgentClient()
    app.state.mm.register("agent", app.state.agent.unload)

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
            if not await orchestrator.advance(db, run, mm=app.state.mm, comfy=app.state.comfy, vision=app.state.vision, agent=app.state.agent):
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
    now = utcnow_iso()
    run = {
        "id": "run_" + uuid4().hex[:8],
        "status": RunStatus.DRAFT.value,
        "config": config_dict,
        "domain_profile": None,
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
        "per_class":        {d: 0 for d in config.defect_taxonomy},
        "failed_per_class": {d: 0 for d in config.defect_taxonomy},
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
    if run is None: raise HTTPException(404, "run not found")
    if run["status"] != RunStatus.DRAFT.value:
        raise HTTPException(409, f"can only detect from draft (is {run['status']})")
    try:
        image_path = await database.resolve_source_image(app.state.db, run)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if run["domain_profile"] is None:
        cfg = run["config"]
        async with app.state.mm.use("vision"):
            image_description = await app.state.vision.describe(image_path)
        async with app.state.mm.use("agent"):
            semantic = await app.state.agent.derive_profile(
                image_description, cfg["dataset_description"],
                cfg.get("placement", ""), cfg["defect_taxonomy"])
        run["domain_profile"] = derive_domain_profile(cfg, image_description, semantic)
        await database.update_run(app.state.db, run)

    import shutil
    loop = asyncio.get_running_loop()
    masks_dir = orchestrator.RUNS_DIR / run["id"] / "masks"
    if masks_dir.exists():
        shutil.rmtree(masks_dir)                          # clear once for all buckets

    feasibility = run["domain_profile"].get("feasibility", {})
    def _feasible(d):
        return feasibility.get(d, {}).get("feasible", True)

    all_regions = []
    async with app.state.mm.use("sam3"):
        for bucket in run["domain_profile"]["buckets"]:
            mode = bucket["region_mode"]
            if all(not _feasible(d) for d in bucket["defects"]):       # skip all-infeasible buckets
                bucket["detect_prompt"] = None
                bucket["detect_attempts"] = []
                bucket["skipped"] = "all defects structural / infeasible"
                continue

            def _score(ms, mode=mode):
                if mode == "subdivide":
                    return max((int((m > 0).sum()) for m in ms), default=0)   # largest SINGLE mask
                return len(ms)

            best_prompt, best_masks, attempts = None, [], []
            for cand in bucket["detect_prompts"]:
                m = await app.state.sam3.detect(image_path, cand, bucket["detect_conf"])
                attempts.append({"prompt": cand, "count": len(m), "score": _score(m)})
                if _score(m) > _score(best_masks):
                    best_prompt, best_masks = cand, m
            bucket["detect_prompt"]   = best_prompt or bucket["detect_prompts"][0]
            bucket["detect_attempts"] = attempts

            if mode == "subdivide" and best_masks:
                best_masks = [max(best_masks, key=lambda m: int((m > 0).sum()))]   # grid the object only
                obj_dir = orchestrator.RUNS_DIR / run["id"] / "object" / bucket["id"]
                bucket["object_mask_paths"] = await loop.run_in_executor(
                    None, orchestrator.save_object_masks, obj_dir, best_masks)

            regs = await loop.run_in_executor(
                None, orchestrator.masks_to_regions, run, best_masks, bucket)
            all_regions.extend(regs)

    run["regions"] = all_regions
    await app.state.mm.free_all()
    run = await orchestrator.sync_run(app.state.db, run)
    return {"run_status": run["status"], "regions": run["regions"], "profile": run["domain_profile"]}


@app.post("/runs/{run_id}/approve-mask")
async def approve_mask(run_id: str, body: ApproveMaskRequest):
    run = await database.get_run(app.state.db, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if run["status"] != RunStatus.AWAITING_MASK_REVIEW.value:
        raise HTTPException(409, f"can only approve at mask review (is {run['status']})")

    if body.region_ids is not None:
        rset = set(body.region_ids)
        run["regions"] = [r for r in (run["regions"] or []) if r["id"] in rset]

    disabled = set(body.disabled_defects or [])
    run["progress"]["disabled_defects"] = list(disabled)

    eligible = orchestrator._eligible_defects(run, disabled)
    if not eligible:
        raise HTTPException(400, "no eligible defects after pruning — enable at least one defect "
                            "with a non-empty region pool before approving")

    run["progress"]["mask_approved"] = True
    run = await orchestrator.sync_run(app.state.db, run)
    kick_loop(run["id"])
    return {"run_status": run["status"]}


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


@app.post("/runs/{run_id}/export")
async def export_run(run_id: str):
    run = await database.get_run(app.state.db, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if run["status"] not in (RunStatus.AWAITING_EXPORT.value, RunStatus.COMPLETED.value):
        raise HTTPException(409, f"run not ready to export (is {run['status']})")

    entries = await database.list_dataset_entries(app.state.db, run_id)
    if not entries:
        raise HTTPException(400, "no accepted candidates to export")

    src = await database.get_source_image(app.state.db, run["config"]["source_image_ids"][0])
    if src is None:
        raise HTTPException(400, "source image missing; cannot determine dimensions")

    summary = await asyncio.get_running_loop().run_in_executor(
        None, lambda: export_mod.build_coco_export(
            run, entries, width=src["width"], height=src["height"]))

    run["status"] = RunStatus.COMPLETED.value
    await orchestrator.sync_run(app.state.db, run)   # COMPLETED is terminal/sticky → persists + publishes
    return {"run_status": run["status"], "export": summary}


@app.get("/runs/{run_id}/export/download")
async def download_export(run_id: str):
    zip_path = export_mod.EXPORTS_DIR / f"{run_id}.zip"
    if not zip_path.exists():
        raise HTTPException(404, "no export found — run export first")
    return FileResponse(zip_path, media_type="application/zip",
                        filename=f"{run_id}_dataset.zip")


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


class RegridRequest(BaseModel):
    bucket_id: str
    rows: int = 3
    cols: int = 3


@app.post("/runs/{run_id}/regrid")
async def regrid(run_id: str, req: RegridRequest):
    run = await database.get_run(app.state.db, run_id)
    if run is None: raise HTTPException(404, "run not found")
    if run["status"] != RunStatus.AWAITING_MASK_REVIEW.value:
        raise HTTPException(409, f"can only regrid at mask review (is {run['status']})")

    bucket = next((b for b in run["domain_profile"]["buckets"] if b["id"] == req.bucket_id), None)
    if bucket is None:
        raise HTTPException(404, f"bucket {req.bucket_id!r} not found")
    if bucket["region_mode"] != "subdivide" or not bucket.get("object_mask_paths"):
        raise HTTPException(400, "regrid only applies to subdivide buckets")

    bucket["grid"] = {"rows": max(1, min(req.rows, 8)), "cols": max(1, min(req.cols, 8))}

    # clean stale PNGs for this bucket only
    masks_dir = orchestrator.RUNS_DIR / run["id"] / "masks"
    for f in masks_dir.glob(f"{req.bucket_id}_*.png"):
        f.unlink(missing_ok=True)

    loop = asyncio.get_running_loop()
    masks = await loop.run_in_executor(
        None, orchestrator.load_object_masks, bucket["object_mask_paths"])
    new_regs = await loop.run_in_executor(
        None, orchestrator.masks_to_regions, run, masks, bucket)

    # replace only this bucket's regions in the flat list
    run["regions"] = [r for r in (run["regions"] or []) if r["bucket"] != req.bucket_id] + new_regs

    run = await orchestrator.sync_run(app.state.db, run)
    return {"regions": run["regions"], "grid": bucket["grid"]}


_ARTIFACT_KEYS = {
    "output": "output_path",
    "mask":   "mask_path",
    "input":  "input_path",
    "source": "source_path",
}

@app.get("/runs/{run_id}/candidates/{cid}/artifact/{kind}")
async def get_candidate_artifact(run_id: str, cid: str, kind: str):
    key = _ARTIFACT_KEYS.get(kind)
    if key is None:
        raise HTTPException(404, f"unknown artifact kind: {kind}")
    cand = await database.get_candidate(app.state.db, cid)
    if cand is None or cand["run_id"] != run_id:
        raise HTTPException(404, "candidate not found")
    path = (cand.get("artifacts") or {}).get(key)
    if not path or not Path(path).exists():
        raise HTTPException(404, f"artifact '{kind}' not available")
    return FileResponse(path)


SOURCES_DIR = Path(__file__).resolve().parent.parent / "data" / "sources"
SOURCES_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/sources", status_code=201)
async def upload_source(file: UploadFile = File(...)):
    img_id = "src_" + uuid4().hex[:8]
    path = SOURCES_DIR / f"{img_id}{Path(file.filename or '').suffix.lower() or '.png'}"
    path.write_bytes(await file.read())
    try:
        with Image.open(path) as im:
            w, h = im.size
    except Exception:
        path.unlink(missing_ok=True)
        raise HTTPException(400, "not a readable image")
    rec = {"id": img_id, "path": str(path), "width": w, "height": h,
           "uploaded_at": utcnow_iso()}
    await database.insert_source_image(app.state.db, rec)
    return rec


@app.get("/sources")
async def list_sources():
    return await database.list_source_images(app.state.db)


@app.get("/sources/{image_id}")
async def get_source(image_id: str):
    img = await database.get_source_image(app.state.db, image_id)
    if img is None:
        raise HTTPException(404, "source image not found")
    return img


@app.get("/sources/{image_id}/file")
async def get_source_file(image_id: str):
    img = await database.get_source_image(app.state.db, image_id)
    if img is None:
        raise HTTPException(404, "source image not found")
    return FileResponse(img["path"])


async def resolve_source_image(run: dict) -> str:
    ids = run["config"].get("source_image_ids") or []
    if not ids:
        raise HTTPException(400, "run has no source_image_ids")
    img = await database.get_source_image(app.state.db, ids[0])
    if img is None:
        raise HTTPException(404, f"source image {ids[0]} not found")
    return img["path"]


# --- Debug endpoints below


@app.post("/debug/candidates/{cid}/step")
async def debug_step_candidate(cid: str):
    cand = await database.get_candidate(app.state.db, cid)
    if cand is None:
        raise HTTPException(404, "candidate not found")
    run  = await database.get_run(app.state.db, cand["run_id"])
    cand = await orchestrator.step_candidate(app.state.db, run, cand, mm=app.state.mm, comfy=app.state.comfy, vision=app.state.vision, agent=app.state.agent)
    run  = await orchestrator.sync_run(app.state.db, run)
    return {"candidate_status": cand["status"], "run_status": run["status"],
            "progress": run["progress"]}

