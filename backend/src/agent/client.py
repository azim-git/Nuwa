import asyncio, logging, subprocess
logger = logging.getLogger("nuwa.agent")

class AgentClient:
    def __init__(self, model: str = "qwen3:8b"):
        self.model = model

    def _author_sync(self, dataset_description, defect_type, feedback, guidance, strategy) -> str:
        import ollama
        extra = ""
        if strategy:
            extra += f"\nVisual character of this defect on this material: {strategy}."
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

    async def author(self, dataset_description, defect_type, *, strategy=None, feedback=None, guidance=None) -> str:
        return await asyncio.get_running_loop().run_in_executor(
            None, self._author_sync, dataset_description, defect_type, feedback, guidance, strategy)

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
    
    def _derive_profile_sync(self, image_description, dataset_description,
                             placement, defect_taxonomy) -> dict:
        import ollama, json
        prompt = (
            "You are configuring a synthetic defect pipeline for ONE product image.\n\n"
            f"Image (from a vision model):\n{image_description}\n\n"
            f"Product: {dataset_description}\n"
            f"Defect types: {', '.join(defect_taxonomy)}\n"
            f"User placement hint (apply where relevant): {placement}\n\n"
            "Do four things:\n\n"
            "A) Group the defects into PLACEMENT BUCKETS by WHERE each naturally occurs on this "
            "object. Defects in the same kind of location share a bucket (surface corrosion and "
            "surface cracks → whole-surface bucket; edge/teeth defects → teeth bucket). Every "
            "defect goes in exactly one bucket.\n\n"
            "B) For each bucket pick region_mode + detect_prompts. CRITICAL: detection runs on the "
            "CLEAN image BEFORE any defect exists, so detect_prompts must name the INTACT feature or "
            "object the defect will be placed ON — NEVER the defect itself. For a teeth bucket use "
            "'gear tooth' / 'metal tooth', NOT 'missing tooth' or 'broken tooth'. For a surface "
            "bucket use 'metal gear' / 'steel disc', NOT 'rust' or 'cracked surface'. These go to an "
            "open-vocab segmentation model that matches on appearance, not names:\n"
            "   - 'instance' (small repeated features): detect_prompts are 2-3 word phrases naming "
            "the INTACT feature, [color/size] + [shape noun: circle, hole, tooth, dot, line]. No "
            "prepositions, no functional names, NO defect words. grid = null.\n"
            "   - 'subdivide' (anywhere on one large surface): detect_prompts name the whole CLEAN "
            "object as a plain noun ('metal gear', 'steel disc'). Give grid {\"rows\":N,\"cols\":N}, "
            "2-4 each.\n"
            "   detect_prompts is a ranked list of 2-4 either way.\n\n"
            "C) Flag each defect's feasibility for REGION INPAINTING (painting into a masked patch):\n"
            "   - feasible=true for SURFACE/appearance defects (rust, scratch, crack, discoloration, "
            "pitting, burn).\n"
            "   - feasible=false for STRUCTURAL defects that change geometry or silhouette (missing "
            "teeth, broken edge, bent/deformed shape) — inpainting cannot remove material or alter an "
            "outline. Give a short reason.\n\n"
            "D) For each defect, one short phrase for its physical appearance on this material.\n\n"
            'Return ONLY JSON:\n'
            '{"buckets":[{"id":"surface","defects":["..."],"region_mode":"instance|subdivide",'
            '"detect_prompts":["..."],"grid":{"rows":3,"cols":3}}],'
            '"feasibility":{"<defect>":{"feasible":true,"reason":""}},'
            '"defect_strategies":{"<defect>":"<phrase>"},"detect_rationale":"<one sentence>"}'
        )
        fallback = {
            "buckets": [{"id": "bucket_0", "defects": list(defect_taxonomy),
                         "region_mode": "instance",
                         "detect_prompts": ["small circle", "round hole"], "grid": None}],
            "feasibility": {d: {"feasible": True, "reason": ""} for d in defect_taxonomy},
            "defect_strategies": {d: f"realistic {d}" for d in defect_taxonomy},
            "detect_rationale": "fallback: agent derivation failed",
        }
        try:
            raw = ollama.chat(model=self.model, messages=[{"role": "user", "content": prompt}],
                              options={"temperature": 0.3}, keep_alive=0, think=False)["message"]["content"]
            clean = raw.strip()
            if "</think>" in clean: clean = clean.split("</think>")[-1].strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"): clean = clean[4:]
            d = json.loads(clean.strip())

            buckets, assigned = [], set()
            for i, b in enumerate(d.get("buckets") or []):
                defs = [x for x in (b.get("defects") or []) if x in defect_taxonomy and x not in assigned]
                prompts = [p.strip() for p in (b.get("detect_prompts") or [])
                           if isinstance(p, str) and p.strip()]
                if not defs or not prompts:
                    continue
                mode = (b.get("region_mode") or "instance").strip().lower()
                if mode not in ("instance", "subdivide"):
                    mode = "instance"
                if mode == "subdivide":
                    g = b.get("grid") or {}
                    try:
                        grid = {"rows": max(1, min(int(g.get("rows", 3)), 6)),
                                "cols": max(1, min(int(g.get("cols", 3)), 6))}
                    except Exception:
                        grid = {"rows": 3, "cols": 3}
                else:
                    grid = None
                buckets.append({"id": b.get("id") or f"bucket_{i}", "defects": defs,
                                "region_mode": mode, "detect_prompts": prompts[:4], "grid": grid})
                assigned.update(defs)

            leftover = [d_ for d_ in defect_taxonomy if d_ not in assigned]
            if leftover:
                buckets.append({"id": f"bucket_{len(buckets)}", "defects": leftover,
                                "region_mode": "instance",
                                "detect_prompts": ["small circle", "round hole"], "grid": None})
            if not buckets:
                return fallback

            feas = d.get("feasibility") or {}
            strat = d.get("defect_strategies") or {}

            # Force unassigned defects to infeasible — placement unclear
            for d_ in leftover:
                feas[d_] = {"feasible": False,
                            "reason": "not assigned to any placement bucket — placement unclear"}

            return {
                "buckets": buckets,
                "feasibility": {d_: {"feasible": bool((feas.get(d_) or {}).get("feasible", True)),
                                     "reason": ((feas.get(d_) or {}).get("reason") or "").strip()}
                                for d_ in defect_taxonomy},
                "defect_strategies": {d_: (strat.get(d_) or f"realistic {d_}").strip()
                                      for d_ in defect_taxonomy},
                "detect_rationale": (d.get("detect_rationale") or "").strip(),
            }
        except Exception as e:
            logger.warning("derive_profile failed (%s); using fallback", e)
            return fallback

    async def derive_profile(self, image_description, dataset_description,
                             placement, defect_taxonomy) -> dict:
        return await asyncio.get_running_loop().run_in_executor(
            None, self._derive_profile_sync,
            image_description, dataset_description, placement, defect_taxonomy)