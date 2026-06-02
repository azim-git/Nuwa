import os, asyncio, logging
from pathlib import Path
logger = logging.getLogger("nuwa.sam3")

DEFAULT_MODEL = os.getenv("SAM3_MODEL_PATH", str(Path(__file__).resolve().parent.parent.parent / "models" / "sam3.pt"))
_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"

class Sam3Client:
    """ultralytics SAM3SemanticPredictor — loaded per detect, freed on GPU handoff."""

    def __init__(self, model_path: str = DEFAULT_MODEL, device: str = "0") -> None:
        self._model_path = model_path
        self._device = device
        self._predictor = None

    def _detect_sync(self, image_path, prompt, conf):
        import numpy as np
        from ultralytics.models.sam import SAM3SemanticPredictor
        if self._predictor is None:                    # unload() clears this between runs
            self._predictor = SAM3SemanticPredictor(overrides=dict(
                conf=conf, task="segment", mode="predict",
                model=self._model_path, half=True, device=self._device,
                project=str(_DATA_DIR / "runs" / "segment")))
        self._predictor.set_image(image_path)
        results = self._predictor(text=[prompt])       # ultralytics wants a list
        if not results or results[0].masks is None:
            return []
        masks = results[0].masks.data.cpu().numpy()     # (N, H, W) float probs
        return [(m > 0.5).astype(np.uint8) * 255 for m in masks]

    async def detect(self, image_path, prompt, conf):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._detect_sync, image_path, prompt, conf)

    async def unload(self):
        if self._predictor is not None:
            import torch
            self._predictor = None
            torch.cuda.empty_cache()
            logger.info("SAM3 unloaded")