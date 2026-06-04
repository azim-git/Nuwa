# scratch_describe.py
import asyncio
from pathlib import Path
from src.vision.client import VisionClient

_IMG = str(Path(__file__).resolve().parent.parent / "pcb_regular_resized.png")

async def main():
    v = VisionClient()
    print(await v.describe(_IMG))
    await v.unload()

asyncio.run(main())