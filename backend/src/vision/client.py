import asyncio, base64, io, json, logging, subprocess
from pathlib import Path
logger = logging.getLogger("nuwa.vision")

class VisionClient:
    def __init__(self, model: str = "qwen2.5vl:7b"):
        self.model = model

    def _score_sync(self, image_path, defect_type, rubric, bbox):
        import ollama
        from PIL import Image
        img = Image.open(image_path).convert("RGB")
        x, y, w, h = bbox
        pad = max(w, h)                                   # focus on the defect + context
        crop = img.crop((max(0, x - pad), max(0, y - pad),
                         min(img.width, x + w + pad), min(img.height, y + h + pad)))
        if max(crop.size) < 256:                          # upscale tiny crops for the VLM
            s = 256 / max(crop.size)
            crop = crop.resize((int(crop.width * s), int(crop.height * s)))
        buf = io.BytesIO(); crop.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        prompt = (
            f'A synthetic "{defect_type}" defect was added to this cropped region of a '
            f'manufactured part. Judge it on: {"; ".join(rubric)}. '
            'Return ONLY JSON, no prose: '
            '{"realism": <0.0-1.0>, "matches_class": <true|false>, '
            '"verdict": "<pass|fail>", "reason": "<one sentence>"}'
        )
        raw = ollama.chat(model=self.model,
                          messages=[{"role": "user", "content": prompt, "images": [b64]}],
                          options={"temperature": 0.1}, think=False)["message"]["content"]

        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        try:
            d = json.loads(clean.strip())
            realism = float(d.get("realism", 0.0))
            score = round(realism if d.get("matches_class") else realism * 0.5, 2)
            return {"vision_score": score, "vision_verdict": d.get("verdict", "fail"),
                    "reason": d.get("reason", "")}
        except Exception:
            return {"vision_score": 0.0, "vision_verdict": "parse_error", "reason": raw[:200]}

    async def score(self, image_path, defect_type, rubric, bbox):
        return await asyncio.get_running_loop().run_in_executor(
            None, self._score_sync, image_path, defect_type, rubric, bbox)

    async def unload(self):                               # your "stop Ollama" rule, automated
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: subprocess.run(["ollama", "stop", self.model], capture_output=True))
        logger.info("vision model stopped")