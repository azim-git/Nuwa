# scratch_derive.py — test C1 bucketed derivation + feasibility
import asyncio
from src.agent.client import AgentClient

# Gear description (you'll fill this in with your actual image description from vision)
GEAR_DESC = ("The main object is a metal gear with a circular disc body and radial teeth around the perimeter. "
             "The disc surface is smooth steel. The teeth are evenly spaced, triangular shaped, with sharp edges. "
             "The center has a round hub.")

async def main():
    a = AgentClient()
    out = await a.derive_profile(
        image_description=GEAR_DESC,
        dataset_description="a metal gear",
        placement="on the gear",
        defect_taxonomy=["crack", "rust", "missing teeth", "bad manufacture", "deformation"],
    )
    import json

    # Print buckets for review
    print("\n=== BUCKETS ===")
    print(json.dumps(out.get("buckets"), indent=2))

    # Print feasibility for review
    print("\n=== FEASIBILITY ===")
    print(json.dumps(out.get("feasibility"), indent=2))

    # Print strategies for reference
    print("\n=== DEFECT STRATEGIES ===")
    print(json.dumps(out.get("defect_strategies"), indent=2))

    await a.unload()

asyncio.run(main())