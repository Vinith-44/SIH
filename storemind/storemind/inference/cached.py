"""Replay a detector's recorded output, with confidences.

`eval/detcache.py` runs the real detector once per clip and records every box
with its score for every frame the pipeline would process.  Replaying that file
means a tracker or counter experiment changes *only* the component under test
(research/26 section 4.9) and runs in seconds instead of re-running inference.

Unlike `ScriptedDetector` (synthetic truth boxes, fixed score), this keeps the
real scores - ByteTrack, OC-SORT and BoT-SORT all split detections by score, so
a fixed score would quietly change what is being compared.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .detector import Detection, Detector


class CachedDetector(Detector):
    name = "cached"

    def __init__(self, cache: str | Path | dict, conf: float = 0.25) -> None:
        payload = cache if isinstance(cache, dict) else json.loads(Path(cache).read_text(encoding="utf-8"))
        self.meta = {k: v for k, v in payload.items() if k not in ("frames", "times")}
        self.times: dict[int, float] = {int(k): v for k, v in payload.get("times", {}).items()}
        if conf < payload.get("conf_floor", 0.0) - 1e-9:
            raise ValueError(f"cache was recorded at conf >= {payload['conf_floor']}, "
                             f"cannot replay at {conf}")
        self.frames: dict[int, list[list[float]]] = {int(k): v for k, v in payload["frames"].items()}
        self.conf = conf
        self.model_name = f"cached:{payload.get('model')}@{payload.get('imgsz')}"
        self.input_size = payload.get("imgsz")
        self._index = 0

    def seek(self, frame_index: int) -> None:
        self._index = frame_index

    def detect(self, image: np.ndarray) -> list[Detection]:
        if self._index not in self.frames:
            raise KeyError(f"frame {self._index} not in the detection cache - it was recorded "
                           "with a different FPS schedule; rebuild it")
        return [Detection((b[0], b[1], b[2], b[3]), b[4], int(b[5]) if len(b) > 5 else 0)
                for b in self.frames[self._index] if b[4] >= self.conf]


class CachedTimeline:
    """A `FrameSource` that replays only the frames in a detection cache, with
    their original video timestamps and a tiny blank image.

    Fed to the pipeline's `FpsScheduler`, exactly these frames are accepted
    again (each is at least one period after the previous), so a tracker or
    counter experiment runs the real pipeline without decoding any video.
    Only valid with `CachedDetector` - the image is blank.
    """

    def __init__(self, detector: CachedDetector, name: str = "cached") -> None:
        if not detector.times:
            raise ValueError("this cache has no frame timestamps; rebuild it")
        self.name = name
        width, height = detector.meta.get("size") or (384, 288)
        self._image = np.zeros((height, width, 3), dtype=np.uint8)
        self._order = sorted(detector.times)
        self._times = detector.times
        self._pos = 0

    def read(self):
        from ..ingest.sources import Frame

        if self._pos >= len(self._order):
            return None
        index = self._order[self._pos]
        self._pos += 1
        return Frame(image=self._image, index=index, video_s=self._times[index], wall_s=0.0)

    def close(self) -> None:
        pass
