DEFAULT_DETECT_CONF = 0.1
DEFAULT_DILATION_PX = 5

_EVAL_RUBRIC = [
    "defect is visually realistic",
    "defect is localised to the masked region",
    "background is unchanged",
    "defect matches the named class",
]


def derive_domain_profile(config: dict, image_description: str, semantic: dict) -> dict:
    """Assemble the operational profile from the agent's bucketed reasoning + pipeline defaults.

    `semantic` carries buckets (with detect_prompts, region_mode, grid per bucket),
    feasibility, defect_strategies, and detect_rationale.
    """
    buckets = []
    for b in semantic["buckets"]:
        buckets.append({
            "id":             b["id"],
            "defects":        b["defects"],
            "region_mode":    b["region_mode"],
            "detect_prompts": b["detect_prompts"],
            "detect_prompt":  None,                 # winner — chosen at detect (C2)
            "detect_conf":    DEFAULT_DETECT_CONF,
            "grid":           b["grid"],
            "dilation_px":    DEFAULT_DILATION_PX,
        })
    return {
        "derived_by": "agent",
        "image_description": image_description,
        "buckets": buckets,
        "feasibility": semantic["feasibility"],
        "defect_strategies": semantic["defect_strategies"],
        "detect_rationale": semantic.get("detect_rationale", ""),
        "eval_rubric": _EVAL_RUBRIC,
    }