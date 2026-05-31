import logging
from contextlib import asynccontextmanager

logger = logging.getLogger("nuwa.vram")

CONSUMERS = {"agent", "sam3", "comfyui", "vision"}


class ModelManager:
    """Enforces one heavy GPU consumer at a time (12GB ceiling).

    The §3 choreography is structural: acquiring a slot frees whatever
    currently holds the GPU first, so a stage cannot skip the free step.
    """

    def __init__(self) -> None:
        self._resident: str | None = None

    @property
    def resident(self) -> str | None:
        return self._resident

    @asynccontextmanager
    async def use(self, consumer: str):
        if consumer not in CONSUMERS:
            raise ValueError(f"unknown GPU consumer: {consumer}")
        if self._resident and self._resident != consumer:
            await self._free(self._resident)
        self._resident = consumer
        logger.info("GPU acquired by %s", consumer)
        yield                       # frees lazily on the next acquire

    async def free_all(self) -> None:
        if self._resident:
            await self._free(self._resident)
            self._resident = None

    async def _free(self, consumer: str) -> None:
        # STUB — Track 3 fills real teardown per consumer:
        #   agent / vision -> `ollama stop <model>`
        #   sam3           -> del model; torch.cuda.empty_cache()
        #   comfyui        -> POST /free (unload models, keep process)
        logger.info("freeing GPU held by %s (stub)", consumer)