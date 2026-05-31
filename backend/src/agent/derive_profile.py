def derive_domain_profile(config: dict) -> dict:
    """Derive the operational profile from the user's config.

    STUB — Track 4 replaces the body with a Qwen3-8B call.
    The signature is the contract; callers never change.
    """
    taxonomy = config["defect_taxonomy"]
    return {
        "derived_by": "stub",
        "mask_prompt": "silver circle",
        "mask_conf": 0.1,
        "dilation_px": 5,
        "defect_strategies": {d: f"realistic {d}" for d in taxonomy},
        "eval_rubric": [
            "defect is visually realistic",
            "defect is localised to the masked region",
            "background is unchanged",
            "defect matches the named class",
        ],
    }