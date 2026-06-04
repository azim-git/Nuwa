# scratch_sam3.py  (run from backend/)
import asyncio
from pathlib import Path
from src.sam3.client import Sam3Client

# grab the on-disk path from GET /sources/src_13c120b0 ("path"), or reuse the original file
IMG = str(Path(__file__).resolve().parent.parent / "pcb_regular_resized.png")

PROMPTS = ["via", "silver circle", "small silver circle", "circular hole",
           "silver circular pad", "round silver hole", "solder pad"]

async def main():
    sam = Sam3Client()
    for p in PROMPTS:
        masks = await sam.detect(IMG, p, 0.1)
        print(f"{p!r:24} -> {len(masks)} masks")
    await sam.unload()

asyncio.run(main())