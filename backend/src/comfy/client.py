# backend/src/comfy/client.py
import os, json, time, asyncio, shutil, logging
from pathlib import Path
import aiohttp, aiofiles

logger = logging.getLogger("nuwa.comfy")
_WF = Path(__file__).resolve().parent / "Inpainting.json"   # PoC workflow template


class ComfyUIClient:
    OUTPUT_NODE = "651"                                       # PoC convention

    def __init__(self, base_url: str = "http://127.0.0.1:8188", input_dir: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.input_dir = Path(input_dir or os.environ["COMFYUI_INPUT_DIR"])

    async def health(self) -> bool:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{self.base_url}/system_stats") as r:
                    return r.status == 200
        except Exception:
            return False

    def _build_workflow(self, input_filename: str, prompt: str) -> dict:
        wf = json.loads(_WF.read_text())
        wf["137"]["inputs"]["image"]   = f"{input_filename} [input]"   # LoadImage
        wf["652:45"]["inputs"]["text"] = prompt                        # positive prompt
        wf["652:44"]["inputs"]["seed"] = int(time.time() * 1000) % (2**32)
        return wf

    async def inpaint(self, rgba_path: str, prompt: str, save_to: str,
                      timeout: int = 300) -> str:
        # stage the RGBA input where ComfyUI's LoadImage can see it
        fname = Path(rgba_path).name
        shutil.copy(rgba_path, self.input_dir / fname)
        wf = self._build_workflow(fname, prompt)

        async with aiohttp.ClientSession() as s:
            async with s.post(f"{self.base_url}/prompt", json={"prompt": wf}) as r:
                pid = (await r.json())["prompt_id"]

            start = time.time()
            while time.time() - start < timeout:
                async with s.get(f"{self.base_url}/history/{pid}") as r:
                    hist = await r.json()
                    if pid in hist:
                        break
                await asyncio.sleep(3)
            else:
                raise TimeoutError(f"ComfyUI timed out after {timeout}s")

            node = hist[pid]["outputs"].get(self.OUTPUT_NODE, {})
            if not node.get("images"):
                raise RuntimeError(f"no image from node {self.OUTPUT_NODE}")
            info = node["images"][0]
            params = f"filename={info['filename']}&type=output"
            if info.get("subfolder"):
                params += f"&subfolder={info['subfolder']}"
            async with s.get(f"{self.base_url}/view?{params}") as r:
                data = await r.read()

        Path(save_to).parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(save_to, "wb") as f:
            await f.write(data)
        return save_to
    
    async def unload(self):
        try:
            async with aiohttp.ClientSession() as s:
                await s.post(f"{self.base_url}/free",
                             json={"unload_models": True, "free_memory": True})
            logger.info("ComfyUI freed")
        except Exception as e:
            logger.warning("ComfyUI free failed: %s", e)