import random
from uuid import uuid4

import database
import util
from models import CandidateStatus as CS

ADVANCEABLE = {CS.PENDING.value, CS.READY.value, CS.EVALUATING.value}
GATE        = {CS.AWAITING_MASK.value, CS.ESCALATED.value}
TERMINAL    = {CS.ACCEPTED.value, CS.REJECTED.value, CS.FAILED.value}


def new_candidate(run: dict) -> dict:
    """Pick a defect type (least-generated) and a random subset of regions."""
    regions = run.get("regions") or []
    dpi = run["config"]["defects_per_image"]
    k = random.randint(dpi["min"], min(dpi["max"], len(regions)))
    chosen = random.sample(regions, k)
    per_class = run["progress"]["per_class"]
    return {
        "id": "cand_" + uuid4().hex[:8],
        "run_id": run["id"],
        "status": CS.PENDING.value,
        "phase": run["progress"]["phase"],
        "defect_type": min(per_class, key=per_class.get),
        "parent_candidate_id": None,
        "region_ids": [r["id"] for r in chosen],
        "prompt": None, "artifacts": None, "evaluation": None,
        "adaptation": None, "human_decision": None, "labels": None,
        "created_at": util.utcnow_iso(),
    }


# ---- mocked stages (Track 3 swaps for real SAM3 / agent / ComfyUI / eval) ----

def _mock_detect_regions(run: dict, n: int = 8) -> list[dict]:
    out = []
    for i in range(n):
        x, y = random.randint(50, 950), random.randint(50, 380)
        out.append({
            "id": f"reg_{i:02d}",
            "bbox": [x, y, 56, 56],
            "mask_path": f"runs/{run['id']}/masks/reg_{i:02d}.png",
            "via_count": 1,
        })
    return out


def _mock_author(run: dict, cand: dict) -> str:
    return run["domain_profile"]["defect_strategies"].get(
        cand["defect_type"], cand["defect_type"])


def _mock_compose(run: dict, cand: dict) -> dict:
    base = f"runs/{run['id']}/{cand['id']}"
    return {
        "input_path":  f"runs/{run['id']}/source.png",
        "mask_path":   f"{base}/composite_mask.png",
        "output_path": None,
    }


def _mock_evaluate(run: dict, cand: dict) -> dict:
    diff   = round(random.uniform(0.30, 0.95), 2)
    vision = round(random.uniform(0.30, 0.95), 2)
    w  = run["config"]["score_weights"]
    th = run["config"]["thresholds"]
    combined = round(diff * w["diff"] + vision * w["vision"], 2)
    if combined >= th["auto_accept_above"]:
        decision = "accept"
    elif combined <= th["auto_reject_below"]:
        decision = "reject"
    else:
        decision = "escalate"
    return {"diff_score": diff, "vision_score": vision, "combined_score": combined,
            "vision_verdict": "mock", "reason": "mock evaluation", "decision": decision}


async def _accept(db, run: dict, cand: dict) -> None:
    region_map = {r["id"]: r for r in (run.get("regions") or [])}
    labels = [
        {"category": cand["defect_type"], "bbox": region_map[rid]["bbox"]}
        for rid in cand["region_ids"] if rid in region_map
    ]
    cand["labels"] = labels
    for lab in labels:
        await database.insert_dataset_entry(db, {
            "id": "ds_" + uuid4().hex[:8],
            "run_id": run["id"], "candidate_id": cand["id"],
            "category": lab["category"], "bbox": lab["bbox"],
            "split": None, "image_path": cand["artifacts"]["output_path"],
        })


# ---- one stage per call, never crosses a gate ----

async def step_candidate(db, run: dict, cand: dict) -> dict:
    s = cand["status"]

    if s == CS.PENDING.value:                        # compose mask + author prompt
        cand["prompt"] = _mock_author(run, cand)
        cand["artifacts"] = _mock_compose(run, cand)
        cand["status"] = CS.READY.value

    elif s == CS.READY.value:                        # inpaint
        cand["artifacts"]["output_path"] = f"runs/{run['id']}/{cand['id']}/output.png"
        cand["status"] = CS.EVALUATING.value

    elif s == CS.EVALUATING.value:                   # evaluate + route
        ev = _mock_evaluate(run, cand)
        cand["evaluation"] = ev
        d = ev["decision"]
        if d == "accept":
            cand["status"] = CS.ACCEPTED.value
            await _accept(db, run, cand)
        elif d == "reject":
            cand["status"] = CS.REJECTED.value
        else:
            cand["status"] = (CS.ESCALATED.value
                              if run["config"]["gates"]["candidate_review"]
                              else CS.REJECTED.value)
    else:
        return cand                                  # gate / terminal

    await database.update_candidate(db, cand)
    return cand