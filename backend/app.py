from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

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
    defect_taxonomy: list[str] = Field(min_length=1)
    eval_prompt: str
    pilot_count: int = 5
    target_count: int = 30
    gates: Gates = Field(default_factory=Gates)
    score_weights: ScoreWeights = Field(default_factory=ScoreWeights)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    on_reject: str = "retry_same_region"
    defects_per_image: DefectsPerImage = Field(default_factory=DefectsPerImage)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = await database.connect()
    await database.init_db(app.state.db)
    app.state.mm = ModelManager()
    app.state.comfy = ComfyUIClient()
    yield                                   # app runs while suspended here
    await app.state.db.close()


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
            "escalated_pending": 0,
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


@app.get("/runs/{run_id}/candidates")
async def get_candidates(run_id: str):
    return await database.list_candidates(app.state.db, run_id)


@app.post("/debug/runs/{run_id}/new-candidate", status_code=201)
async def debug_new_candidate(run_id: str):
    run = await database.get_run(app.state.db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if not run.get("regions"):
        raise HTTPException(status_code=400, detail="no regions yet — call /detect first")
    cand = orchestrator.new_candidate(run)
    await database.insert_candidate(app.state.db, cand)
    return cand


@app.post("/debug/candidates/{cid}/step")
async def debug_step_candidate(cid: str):
    cand = await database.get_candidate(app.state.db, cid)
    if cand is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    run = await database.get_run(app.state.db, cand["run_id"])
    return await orchestrator.step_candidate(app.state.db, run, cand)


@app.post("/debug/runs/{run_id}/detect")
async def debug_detect(run_id: str):
    run = await database.get_run(app.state.db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    run["regions"] = orchestrator._mock_detect_regions(run)
    await database.update_run(app.state.db, run)
    return {"region_count": len(run["regions"]), "regions": run["regions"]}