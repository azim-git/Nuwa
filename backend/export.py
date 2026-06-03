import json
import random
import shutil
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EXPORTS_DIR = DATA_DIR / "exports"


def build_coco_export(run: dict, entries: list[dict], *, width: int, height: int,
                      val_ratio: float = 0.2, seed: int = 0) -> dict:
    """Assemble accepted dataset_entries into a COCO train/val export + zip.
    Sync (file IO) — call via run_in_executor. Idempotent: rebuilds from scratch."""
    run_id = run["id"]
    out_dir = EXPORTS_DIR / run_id
    if out_dir.exists():
        shutil.rmtree(out_dir)                      # clean re-export
    (out_dir / "images").mkdir(parents=True, exist_ok=True)
    (out_dir / "annotations").mkdir(parents=True, exist_ok=True)

    taxonomy = run["config"]["defect_taxonomy"]
    cat_id = {name: i + 1 for i, name in enumerate(taxonomy)}   # COCO ids are 1-indexed
    categories = [{"id": i + 1, "name": name} for i, name in enumerate(taxonomy)]

    # one generated image == one candidate; group its labels (vias)
    by_image = defaultdict(list)
    for e in entries:
        by_image[e["candidate_id"]].append(e)

    # deterministic per-image split
    image_ids = sorted(by_image.keys())
    random.Random(seed).shuffle(image_ids)
    n_val = round(len(image_ids) * val_ratio)
    val_set = set(image_ids[:n_val])

    def empty_coco():
        return {"info": {"description": run["config"]["dataset_description"], "run_id": run_id},
                "images": [], "annotations": [], "categories": categories}

    coco = {"train": empty_coco(), "val": empty_coco()}
    ann_counter = 0
    manifest_images = []

    for img_idx, cand_id in enumerate(sorted(image_ids), start=1):
        rows = by_image[cand_id]
        file_name = f"images/{cand_id}.png"
        shutil.copyfile(rows[0]["image_path"], out_dir / file_name)
        split = "val" if cand_id in val_set else "train"
        coco[split]["images"].append(
            {"id": img_idx, "file_name": file_name, "width": width, "height": height})
        for e in rows:
            ann_counter += 1
            x, y, w, h = e["bbox"]
            coco[split]["annotations"].append({
                "id": ann_counter, "image_id": img_idx,
                "category_id": cat_id.get(e["category"], 0),
                "bbox": [x, y, w, h], "area": float(w * h),
                "iscrowd": 0, "segmentation": [],
            })
        manifest_images.append(
            {"candidate_id": cand_id, "split": split, "n_annotations": len(rows)})

    (out_dir / "annotations" / "instances_train.json").write_text(json.dumps(coco["train"], indent=2))
    (out_dir / "annotations" / "instances_val.json").write_text(json.dumps(coco["val"], indent=2))

    counts = {
        "images": len(image_ids), "annotations": ann_counter,
        "train_images": len(coco["train"]["images"]),
        "val_images": len(coco["val"]["images"]),
    }
    manifest = {
        "run_id": run_id,
        "dataset_description": run["config"]["dataset_description"],
        "categories": taxonomy,
        "image_size": {"width": width, "height": height},
        "val_ratio": val_ratio, "split_seed": seed,
        "counts": counts, "images": manifest_images,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    shutil.make_archive(str(EXPORTS_DIR / run_id), "zip", root_dir=out_dir)
    return {"categories": taxonomy, **counts}