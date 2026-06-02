import asyncio, logging, subprocess
logger = logging.getLogger("nuwa.agent")

class AgentClient:
    def __init__(self, model: str = "qwen3:8b"):
        self.model = model

    def _author_sync(self, dataset_description, defect_type, feedback, guidance) -> str:
        import ollama
        extra = ""
        if feedback:                                    # pilot: reactive adaptation
            extra += "\nAvoid problems seen in rejected attempts: " + "; ".join(feedback) + "."
        if guidance:                                    # full: frozen consolidation guidance
            pos, neg = guidance.get("positive_keywords") or [], guidance.get("negative_constraints") or []
            if pos: extra += "\nEmphasize: " + ", ".join(pos) + "."
            if neg: extra += "\nAvoid: " + ", ".join(neg) + "."
        prompt = (
            "You write prompts for an image inpainting model that adds a single "
            "manufacturing defect to a small masked region of a product photo.\n\n"
            f"Product: {dataset_description}\nDefect to add: {defect_type}\n\n"
            "Write ONE vivid, concrete inpainting prompt as comma-separated visual descriptors "
            "(physical appearance, colour, texture, how it deviates from the intact part). Be "
            "specific and photographic. Describe only the defect's appearance — do not mention "
            f"masks, models, or instructions.{extra}\n\n"
            "Return ONLY the prompt text, no quotes or preamble."
        )
        try:
            raw = ollama.chat(model=self.model, messages=[{"role": "user", "content": prompt}],
                              options={"temperature": 0.7}, keep_alive=0, think=False)["message"]["content"]
            text = raw.strip().strip('"').strip()
            if "</think>" in text:                      # defensive if think leaks
                text = text.split("</think>")[-1].strip()
            return text or f"realistic {defect_type}, {dataset_description}"
        except Exception as e:
            logger.warning("author failed (%s); fallback prompt", e)
            return f"realistic {defect_type}, {dataset_description}"

    async def author(self, dataset_description, defect_type, *, feedback=None, guidance=None) -> str:
        return await asyncio.get_running_loop().run_in_executor(
            None, self._author_sync, dataset_description, defect_type, feedback, guidance)

    async def unload(self):
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: subprocess.run(["ollama", "stop", self.model], capture_output=True))
        logger.info("agent model stopped")

    def _consolidate_sync(self, accepts, rejects) -> dict:
        import ollama, json
        acc = "\n".join(f"- {p}" for p in accepts) or "(none)"
        rej = "\n".join(f"- prompt: {p}\n  reason: {r}" for p, r in rejects) or "(none)"
        prompt = (
            "You are tuning prompt guidance for a defect-generation agent after a pilot phase.\n\n"
            f"ACCEPTED defect prompts (produced good defects):\n{acc}\n\n"
            f"REJECTED defect prompts, with the reviewer's reason:\n{rej}\n\n"
            "Produce guidance to steer future prompts toward the accepted ones:\n"
            "- positive_keywords: visual descriptors common to the ACCEPTED prompts, to emphasize.\n"
            "- negative_constraints: problems cited in the REJECTIONS to avoid — include one ONLY if "
            "it does not also describe the accepted prompts (it must distinguish good from bad).\n\n"
            'Return ONLY JSON: {"positive_keywords": ["..."], "negative_constraints": ["..."]}'
        )
        try:
            raw = ollama.chat(model=self.model, messages=[{"role": "user", "content": prompt}],
                              options={"temperature": 0.3}, keep_alive=0, think=False)["message"]["content"]
            clean = raw.strip()
            if "</think>" in clean: clean = clean.split("</think>")[-1].strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"): clean = clean[4:]
            d = json.loads(clean.strip())
            return {"positive_keywords": list(d.get("positive_keywords") or []),
                    "negative_constraints": list(d.get("negative_constraints") or [])}
        except Exception as e:
            logger.warning("consolidate failed (%s); empty guidance", e)
            return {"positive_keywords": [], "negative_constraints": []}

    async def consolidate(self, accepts, rejects) -> dict:
        return await asyncio.get_running_loop().run_in_executor(
            None, self._consolidate_sync, accepts, rejects)