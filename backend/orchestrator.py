import random
from uuid import uuid4

import database, asyncio
import util
import events
from models import CandidateStatus as CS, RunStatus as RS

ADVANCEABLE  = {CS.PENDING.value, CS.READY.value, CS.GENERATING.value, CS.EVALUATING.value}
GATE         = {CS.AWAITING_REVIEW.value}
TERMINAL     = {CS.ACCEPTED.value, CS.REJECTED.value, CS.FAILED.value}
TERMINAL_RUN = {RS.COMPLETED.value, RS.ABORTED.value, RS.FAILED.value}

_REFERENCE_PROMPT = "clean circuit board, no defects"

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


def _mock_author(run: dict, cand: dict, *, feedback=None, guidance=None) -> str:
    base = run["domain_profile"]["defect_strategies"].get(
        cand["defect_type"], cand["defect_type"])
    parts = [base]
    if feedback:                                   # pilot: reactive per-candidate adaptation
        parts.append(f"[avoid: {'; '.join(feedback)}]")
    if guidance and guidance.get("positive_keywords"):   # full: frozen consolidation guidance
        parts.append(f"[emphasize: {', '.join(guidance['positive_keywords'])}]")
    return " ".join(parts)


def _mock_compose(run: dict, cand: dict) -> dict:
    base = f"runs/{run['id']}/{cand['id']}"
    return {
        "input_path":  f"runs/{run['id']}/source.png",
        "mask_path":   f"{base}/composite_mask.png",
        "output_path": None,
    }


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


def _snapshot(run: dict, cands: list[dict]) -> dict:
    return {
        "type": "update",
        "run_id": run["id"],
        "status": run["status"],
        "progress": run["progress"],
        "candidates": cands,
    }


async def _recent_rejection_reasons(db, run: dict, limit: int = 3) -> list[str]:
    reasons = []
    for c in await database.list_candidates(db, run["id"]):
        if c["status"] == CS.REJECTED.value and c.get("human_decision"):
            r = c["human_decision"].get("reason")
            if r:
                reasons.append(r)
    return reasons[-limit:]


# ---- one stage per call, never crosses a gate ----

async def step_candidate(db, run: dict, cand: dict, *, mm, comfy, vision) -> dict:
    s = cand["status"]

    if s == CS.PENDING.value:                    # author prompt + composite mask
        if cand["phase"] == "pilot":
            feedback = await _recent_rejection_reasons(db, run)
            cand["prompt"]     = _mock_author(run, cand, feedback=feedback)
            cand["adaptation"] = {"based_on": feedback} if feedback else None
        else:
            guidance = run["domain_profile"].get("prompt_guidance")
            cand["prompt"]     = _mock_author(run, cand, guidance=guidance)
            cand["adaptation"] = None
        source_path = await database.resolve_source_image(db, run)
        cand["artifacts"] = await asyncio.get_running_loop().run_in_executor(None, compose_inputs, run, cand, source_path)
        cand["status"]    = CS.READY.value

    elif s == CS.READY.value:                    # submit inpaint
        cand["status"] = CS.GENERATING.value

    elif s == CS.GENERATING.value:                       # clean reference (once) + defect pass
        run_dir = RUNS_DIR / run["id"]
        ref = _reference_path(run)
        async with mm.use("comfyui"):
            if not ref.exists():                         # the +1, once per run
                src = await database.resolve_source_image(db, run)
                rgba = await asyncio.get_running_loop().run_in_executor(
                    None, _build_keep_all_rgba, src, str(run_dir / "reference_input.png"))
                await comfy.inpaint(rgba, _REFERENCE_PROMPT, str(ref))
            out_path = str(run_dir / cand["id"] / "output.png")
            await comfy.inpaint(cand["artifacts"]["input_path"], cand["prompt"], out_path)
        cand["artifacts"]["output_path"] = out_path
        cand["status"] = CS.EVALUATING.value

    elif s == CS.EVALUATING.value:               # evaluate, then route BY PHASE
        cand["evaluation"] = await _evaluate(db, run, cand, mm, vision)
        if cand["phase"] == "pilot":
            cand["status"] = CS.AWAITING_REVIEW.value      # human decides; scores advisory
            await database.update_candidate(db, cand)
            return cand
        combined = cand["evaluation"]["combined_score"]    # full phase: threshold auto-decides
        decision = "accept" if combined >= run["config"]["thresholds"]["auto_accept_above"] else "reject"
        await apply_decision(db, run, cand, decision, by="agent")
        return cand

    else:
        return cand                              # awaiting_review / terminal — gate

    await database.update_candidate(db, cand)
    return cand


def recompute_run_status(run: dict, candidates: list[dict]) -> str:
    """Pure derivation of run status from DB state. Terminal states are sticky."""
    if run["status"] in TERMINAL_RUN:
        return run["status"]
    if run.get("error"):
        return RS.FAILED.value
    if run.get("regions") is None:
        return RS.DRAFT.value

    prog = run["progress"]
    if not prog.get("mask_approved"):
        return RS.AWAITING_MASK_REVIEW.value

    if prog["phase"] == "pilot":
        if prog["accepted"] >= prog["pilot_target"]:
            return RS.CONSOLIDATING.value                  # consolidation flips phase→full
        if any(c["status"] == CS.AWAITING_REVIEW.value for c in candidates):
            return RS.AWAITING_PILOT_REVIEW.value
        return RS.GENERATING_PILOT.value

    # full phase
    if prog["accepted"] >= prog["target"]:
        return RS.AWAITING_EXPORT.value
    return RS.RUNNING.value


async def sync_run(db, run: dict) -> dict:
    """Recompute + persist run status from current candidate state."""
    cands = await database.list_candidates(db, run["id"])
    run["progress"]["generated"] = len(cands)
    run["status"] = recompute_run_status(run, cands)
    run["updated_at"] = util.utcnow_iso()
    await database.update_run(db, run)
    events.bus.publish(run["id"], _snapshot(run, cands))
    return run


async def apply_decision(db, run: dict, cand: dict, decision: str,
                         *, by: str, reason: str | None = None) -> None:
    """Resolve a candidate. by='agent' (full phase) or 'human' (pilot review)."""
    prog = run["progress"]
    if decision == "accept":
        cand["status"] = CS.ACCEPTED.value
        await _accept(db, run, cand)                       # builds labels + dataset rows
        prog["accepted"] += 1
        prog["per_class"][cand["defect_type"]] += 1
    else:
        cand["status"] = CS.REJECTED.value                 # pilot rejects = teaching signal, never dataset
        prog["rejected"] += 1
    if by == "human":
        cand["human_decision"] = {"decision": decision, "reason": reason,
                                  "at": util.utcnow_iso()}
    await database.update_candidate(db, cand)


LOOP_DELAY = 0.05  # cooperative yield between units; also makes progress observable
LIVE_RUN   = {RS.GENERATING_PILOT.value, RS.RUNNING.value, RS.CONSOLIDATING.value}


async def _active_candidate(db, run: dict) -> dict | None:
    """The single in-flight candidate (sequential generation → at most one)."""
    for c in await database.list_candidates(db, run["id"]):
        if c["status"] in ADVANCEABLE:
            return c
    return None


async def run_consolidation(db, run: dict) -> None:
    """Pilot→full transition. MOCKED — Track 3 swaps in the agent LLM."""
    cands   = await database.list_candidates(db, run["id"])
    accepts = [c for c in cands if c["status"] == CS.ACCEPTED.value]
    rejects = [c for c in cands if c["status"] == CS.REJECTED.value and c.get("human_decision")]
    # Real impl: cross-reference accept vs reject prompts/reasons → discriminative
    #   guidance (negative_constraints only if a reason is in rejects AND NOT accepts),
    #   then validate the threshold. Frozen here; full phase never revises it.
    run["domain_profile"]["prompt_guidance"] = {
        "positive_keywords": [],
        "negative_constraints": [],
        "validated_threshold": run["config"]["thresholds"]["auto_accept_above"],
        "frozen": True,
        "basis": {"accepts": len(accepts), "rejects": len(rejects)},
    }
    run["progress"]["phase"] = "full"          # the flip that makes CONSOLIDATING non-sticky
    run["updated_at"] = util.utcnow_iso()
    await database.update_run(db, run)


async def advance(db, run: dict, *, mm, comfy, vision) -> bool:
    """One unit of work. Returns True if it progressed, False if gated/terminal."""
    status = run["status"]

    if status == RS.CONSOLIDATING.value:
        await run_consolidation(db, run)
        await sync_run(db, run)                # phase is now 'full' → recomputes to RUNNING
        return True

    if status not in (RS.GENERATING_PILOT.value, RS.RUNNING.value):
        return False                           # draft / awaiting_* / export / terminal

    cand = await _active_candidate(db, run)
    if cand is None:                           # no one in flight → start the next candidate
        cand = new_candidate(run)
        await database.insert_candidate(db, cand)
        await sync_run(db, run)
        return True

    await step_candidate(db, run, cand, mm=mm, comfy=comfy, vision=vision)
    await sync_run(db, run)
    return True


from pathlib import Path
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RUNS_DIR = DATA_DIR / "runs"

def masks_to_regions(run: dict, masks: list) -> list[dict]:
    """SAM3 binary masks → region pool, writing one PNG per via (white via on black)."""
    import numpy as np
    from PIL import Image
    masks_dir = DATA_DIR / "runs" / run["id"] / "masks"
    masks_dir.mkdir(parents=True, exist_ok=True)
    regions, idx = [], 0
    for m in masks:
        ys, xs = np.where(m > 0)
        if xs.size == 0:
            continue                                   # skip empty mask
        x0, y0 = int(xs.min()), int(ys.min())
        rid = f"reg_{idx:03d}"
        mask_path = masks_dir / f"{rid}.png"
        Image.fromarray(m).save(mask_path)             # PoC convention: white=via
        regions.append({
            "id": rid,
            "bbox": [x0, y0, int(xs.max() - x0 + 1), int(ys.max() - y0 + 1)],
            "mask_path": str(mask_path),
            "via_count": 1,
        })
        idx += 1
    return regions


def compose_inputs(run: dict, cand: dict, source_path: str) -> dict:
    """OR-combine the candidate's region masks → dilate → invert → RGBA alpha.
    Writes source.png + composite_mask.png; returns the artifacts dict."""
    import numpy as np, cv2
    from PIL import Image

    out_dir = RUNS_DIR / run["id"] / cand["id"]
    out_dir.mkdir(parents=True, exist_ok=True)

    src = Image.open(source_path).convert("RGB")
    W, H = src.size
    region_map = {r["id"]: r for r in (run.get("regions") or [])}

    union = np.zeros((H, W), dtype=np.uint8)            # white = via, per PoC masks
    for rid in cand["region_ids"]:
        m = np.array(Image.open(region_map[rid]["mask_path"]).convert("L"))
        if m.shape != (H, W):
            m = np.array(Image.fromarray(m).resize((W, H), Image.NEAREST))
        union = np.maximum(union, m)

    px = run["domain_profile"]["dilation_px"]           # compose-time, from the profile
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (px * 2 + 1, px * 2 + 1))
    dilated = cv2.dilate(union, kernel, iterations=1)
    alpha = cv2.bitwise_not(dilated)                    # ComfyUI: white=keep, black=inpaint

    source_out = out_dir / "source.png"
    mask_out   = out_dir / "composite_mask.png"
    rgba_out   = out_dir / "rgba_input.png"
    src.save(source_out)
    Image.fromarray(alpha, mode="L").save(mask_out)
    rgba = src.convert("RGBA"); rgba.putalpha(Image.fromarray(alpha, mode="L"))
    rgba.save(rgba_out)

    return {"input_path": str(rgba_out), "mask_path": str(mask_out),
            "output_path": None, "source_path": str(source_out)}


DIFF_GAIN = 1.0   # placeholder — calibrate from real diffs (see below)

def _diff_score(output_path: str, reference_path: str, mask_path: str) -> float:
    """Mean inpaint-vs-inpaint difference inside the defect region, normalised."""
    from PIL import Image, ImageChops
    import numpy as np
    out = Image.open(output_path).convert("RGB")
    ref = Image.open(reference_path).convert("RGB")
    w, h = min(out.width, ref.width), min(out.height, ref.height)
    out, ref = out.crop((0, 0, w, h)), ref.crop((0, 0, w, h))     # per your PoC

    diff = np.asarray(ImageChops.difference(out, ref), dtype=np.float32).mean(axis=2)
    mask = np.asarray(Image.open(mask_path).convert("L").resize((w, h), Image.NEAREST))
    region = mask < 128                                           # black = inpaint area
    if region.sum() == 0:
        return 0.0
    raw = float(diff[region].mean()) / 255.0
    return round(min(1.0, raw * DIFF_GAIN), 4)


def _union_bbox(boxes):
    x0 = min(b[0] for b in boxes); y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes); y1 = max(b[1] + b[3] for b in boxes)
    return [x0, y0, x1 - x0, y1 - y0]


async def _evaluate(db, run, cand, mm, vision) -> dict:
    ref = _reference_path(run)
    diff = None
    if ref.exists():
        diff = await asyncio.get_running_loop().run_in_executor(
            None, _diff_score, cand["artifacts"]["output_path"],
            str(ref), cand["artifacts"]["mask_path"])

    rmap = {r["id"]: r for r in (run.get("regions") or [])}
    boxes = [rmap[rid]["bbox"] for rid in cand["region_ids"] if rid in rmap]
    async with mm.use("vision"):                          # frees ComfyUI first
        v = await vision.score(cand["artifacts"]["output_path"], cand["defect_type"],
                               run["domain_profile"]["eval_rubric"], _union_bbox(boxes))

    w = run["config"]["score_weights"]
    combined = round((diff or 0.0) * w["diff"] + v["vision_score"] * w["vision"], 2)
    return {"diff_score": diff, "vision_score": v["vision_score"], "combined_score": combined,
            "vision_verdict": v["vision_verdict"], "reason": v["reason"]}


def _build_keep_all_rgba(source_path: str, out_path: str) -> str:
    from PIL import Image
    img = Image.open(source_path).convert("RGB").convert("RGBA")
    img.putalpha(255)                                   # all-white alpha = keep everything
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path

def _reference_path(run: dict) -> Path:
    return RUNS_DIR / run["id"] / "reference.png"