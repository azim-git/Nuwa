import logging

logger = logging.getLogger("nuwa.comfy")


class ComfyUIClient:
    """Talks to a separately-running ComfyUI process over HTTP (Track 3).

    Output is always retrieved from node 651 (PoC convention).
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8188") -> None:
        self.base_url = base_url

    async def health(self) -> bool:
        # STUB — Track 3: GET /system_stats
        return False

    async def inpaint(self, image_path: str, mask_path: str, prompt: str) -> str:
        # STUB — Track 3: template the workflow JSON, POST /prompt,
        # poll /history/{id}, read the output filename from node 651.
        raise NotImplementedError("ComfyUI inpaint lands in Track 3")