import asyncio, base64, io, json, logging, subprocess
from pathlib import Path
logger = logging.getLogger("nuwa.vision")

class VisionClient:
    def __init__(self, model: str = "qwen2.5vl:7b"):
        self.model = model

    def _score_sync(self, image_path, dataset_description, defect_type, bbox):
        import ollama
        from PIL import Image
        img = Image.open(image_path).convert("RGB")
        x, y, w, h = bbox; pad = max(w, h)
        crop = img.crop((max(0, x-pad), max(0, y-pad),
                         min(img.width, x+w+pad), min(img.height, y+h+pad)))
        if max(crop.size) < 256:
            s = 256 / max(crop.size); crop = crop.resize((int(crop.width*s), int(crop.height*s)))
        buf = io.BytesIO(); crop.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        prompt = (
            f'This is a cropped region of {dataset_description} where a synthetic '
            f'"{defect_type}" defect was added. Judge true/false on each:\n'
            "- has_defect: a clear defect is visible, not an intact/normal region\n"
            "- looks_physical: a real physical defect, not digital noise, blur, or artifact\n"
            "- plausible: colour and texture fit the material, not garish or random\n"
            f"- matches_class: the defect is specifically a {defect_type}\n"
            'Return ONLY this JSON, no other text:\n'
            '{"has_defect": true, "looks_physical": true, "plausible": true, '
            '"matches_class": true, "reason": "<one sentence>"}'
        )
        raw = ollama.chat(model=self.model,
                          messages=[{"role": "user", "content": prompt, "images": [b64]}],
                          options={"temperature": 0.1, "num_ctx": 2048}, keep_alive=0, think=False)["message"]["content"]
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"): clean = clean[4:]
        try:
            d = json.loads(clean.strip())
            if not d.get("matches_class"):
                score = 0.0                              # class gate: wrong class = useless
            else:
                crits = [d.get("has_defect"), d.get("looks_physical"), d.get("plausible")]
                score = round(sum(bool(c) for c in crits) / len(crits), 2)
            return {"vision_score": score,
                    "vision_verdict": "pass" if score >= 0.67 else "fail",
                    "reason": d.get("reason", "")}
        except Exception:
            return {"vision_score": 0.0, "vision_verdict": "parse_error", "reason": raw[:200]}

    async def score(self, image_path, dataset_description, defect_type, bbox):
        loop = asyncio.get_running_loop()
        for attempt in range(3):
            try:
                return await loop.run_in_executor(
                    None, self._score_sync, image_path, dataset_description, defect_type, bbox)
            except Exception as e:
                if attempt < 2 and "memory" in str(e).lower():
                    logger.warning("vision OOM, settling then retrying (%d)", attempt + 1)
                    await asyncio.sleep(3)
                    continue
                raise

    async def unload(self):                               # your "stop Ollama" rule, automated
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: subprocess.run(["ollama", "stop", self.model], capture_output=True))
        logger.info("vision model stopped")

    def _describe_sync(self, image_path: str) -> str:
        import ollama, io, base64
        from PIL import Image
        img = Image.open(image_path).convert("RGB")
        if max(img.size) > 1024:                          # keep the pass quick / in-ctx
            s = 1024 / max(img.size)
            img = img.resize((int(img.width * s), int(img.height * s)))
        buf = io.BytesIO(); img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        prompt = (
            "You are inspecting a clean product image that will be used to synthesise "
            "surface defects. Describe ONLY what is visible, factually and concisely "
            "(2-3 sentences). Report:\n"
            "- the main object and its material / surface\n"
            "- the distinct repeated features on the surface — for EACH kind, give its visual "
            "appearance (geometric shape and color, e.g. 'small silver circles', 'thin copper "
            "lines') and roughly how many are visible. Describe how they LOOK, not their "
            "engineering function\n"
        )
        return ollama.chat(
            model=self.model,
            messages=[{"role": "user", "content": prompt, "images": [b64]}],
            options={"temperature": 0.2, "num_ctx": 2048},
            keep_alive=0, think=False,
        )["message"]["content"].strip()

    async def describe(self, image_path: str) -> str:
        loop = asyncio.get_running_loop()
        for attempt in range(3):
            try:
                return await loop.run_in_executor(None, self._describe_sync, image_path)
            except Exception as e:
                if attempt < 2 and "memory" in str(e).lower():
                    logger.warning("vision describe OOM, settling then retrying (%d)", attempt + 1)
                    await asyncio.sleep(3)
                    continue
                raise