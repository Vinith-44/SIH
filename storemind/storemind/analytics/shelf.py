"""Label-free shelf and planogram monitoring - novelty pillar N2.

Why not "train a detector for each product": a kirana changes SKUs weekly and we
have no labelled data.  Our previous attempt (a 388k-parameter CNN trained from
scratch on 431 images) over-fitted hard - validation loss bottomed at 1.19 by
epoch 10 and climbed to 2.77 by epoch 18 (audit H1).  So this engine recognises
*change against a known-good reference*, not product identity:

1.  Staff draw slots once on a snapshot (`tools/calibrate.py`) and type the SKU
    name and price.  That drawing **is** the planogram.
2.  After restocking they press "Restocked" (dashboard button, or the physical
    STM32 button, `$R`), and we store one reference crop per slot.
3.  Every `period_s` (shelves change slowly - N6 task-aware scheduling):
    *   **occlusion gate** - if a tracked person overlaps the slot, skip the
        frame entirely.  Audit H5: the old code happily alerted on a shopper's
        back.
    *   **fill estimate** - removing packets exposes the flat back panel, so
        texture/edge density falls.  `fill = edge_density(now) / edge_density(reference)`,
        cross-checked against colour-mass and structural similarity.
    *   **planogram check** - a slot that is full but does not *look* like its
        reference holds the wrong product.
    *   **K-of-N voting** - state changes only when K of the last N gated
        observations agree.  Audit H6: the old code re-alerted every 10 seconds.

`method="detector"` swaps the fill estimate for a class-agnostic product/gap
detector (SKU-110K + gap datasets, trained by
`storemind/train/shelf_detector_kaggle.ipynb`) counting facings per slot.  The
state machine, gating and voting are identical either way.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..core.clock import Clock
from ..core.events import Event, EventType, SlotState, SlotStateData, make_event
from ..core.geometry import Polygon
from ..tracking.tracker import Track

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Image measures (all numpy/OpenCV - no training, runs in ms on a Pi)
# --------------------------------------------------------------------------- #

def edge_density(crop: np.ndarray) -> float:
    """Fraction of pixels on an edge.  Packets have printed faces and box edges;
    an empty slot shows a flat back panel."""
    if crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(gray, 60, 160)
    return float(np.count_nonzero(edges)) / float(edges.size)


def colour_mass(crop: np.ndarray) -> float:
    """Mean saturation.  Product packaging is colourful; shelf liners are not."""
    if crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    return float(hsv[..., 1].mean()) / 255.0


def ssim(a: np.ndarray, b: np.ndarray, size: int = 64) -> float:
    """Global structural similarity on grayscale.  Implemented here so the Pi
    image does not need scikit-image."""
    if a.size == 0 or b.size == 0:
        return 0.0
    ga = cv2.resize(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY) if a.ndim == 3 else a, (size, size)).astype(np.float64)
    gb = cv2.resize(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY) if b.ndim == 3 else b, (size, size)).astype(np.float64)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_a, mu_b = ga.mean(), gb.mean()
    va, vb = ga.var(), gb.var()
    cov = ((ga - mu_a) * (gb - mu_b)).mean()
    numerator = (2 * mu_a * mu_b + c1) * (2 * cov + c2)
    denominator = (mu_a ** 2 + mu_b ** 2 + c1) * (va + vb + c2)
    return float(numerator / denominator) if denominator > 1e-12 else 0.0


class Embedder:
    """Appearance descriptor for the planogram check.

    `classical` (default): HSV colour histogram + gradient-orientation histogram,
    L2-normalised.  No weights, no download, ~0.3 ms per crop - important because
    the Pi has to do this for every slot.

    `mobilenet`: torchvision MobileNetV3-Small penultimate features, used when
    accuracy matters more than speed and weights are available offline.
    """

    def __init__(self, kind: str = "classical") -> None:
        self.kind = kind
        self._model = None
        if kind == "mobilenet":
            try:
                self._load_mobilenet()
            except Exception:
                log.warning("mobilenet embedder unavailable, using classical descriptor")
                self.kind = "classical"

    def _load_mobilenet(self) -> None:
        import torch
        from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

        weights = MobileNet_V3_Small_Weights.DEFAULT
        model = mobilenet_v3_small(weights=weights)
        model.classifier = torch.nn.Identity()
        model.eval()
        self._model = model
        self._transform = weights.transforms()
        self._torch = torch

    def __call__(self, crop: np.ndarray) -> np.ndarray:
        if crop.size == 0:
            return np.zeros(1, dtype=np.float32)
        if self.kind == "mobilenet" and self._model is not None:
            return self._mobilenet_features(crop)
        return self._classical_features(crop)

    def _mobilenet_features(self, crop: np.ndarray) -> np.ndarray:
        torch = self._torch
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        tensor = torch.from_numpy(rgb).permute(2, 0, 1)
        with torch.no_grad():
            features = self._model(self._transform(tensor).unsqueeze(0))[0].numpy()
        norm = np.linalg.norm(features)
        return features / norm if norm > 1e-9 else features

    @staticmethod
    def _classical_features(crop: np.ndarray) -> np.ndarray:
        small = cv2.resize(crop, (64, 64), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        colour = cv2.calcHist([hsv], [0, 1], None, [12, 8], [0, 180, 0, 256]).flatten()
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        magnitude = np.hypot(gx, gy)
        angle = (np.rad2deg(np.arctan2(gy, gx)) + 180.0) % 180.0
        gradient, _ = np.histogram(angle, bins=18, range=(0, 180), weights=magnitude)
        vector = np.concatenate([colour / (colour.sum() + 1e-9),
                                 gradient / (gradient.sum() + 1e-9)]).astype(np.float32)
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm > 1e-9 else vector


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a.size != b.size or a.size == 0:
        return 0.0
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denominator) if denominator > 1e-9 else 0.0


# --------------------------------------------------------------------------- #
# Slot state machine
# --------------------------------------------------------------------------- #

@dataclass
class SlotSpec:
    polygon: Polygon
    sku: str | None = None
    price: float | None = None
    reference_facings: int | None = None


@dataclass
class _SlotRuntime:
    spec: SlotSpec
    reference_crop: np.ndarray | None = None
    reference_edges: float | None = None
    reference_colour: float | None = None
    reference_embedding: np.ndarray | None = None
    votes: deque = field(default_factory=lambda: deque(maxlen=5))
    state: SlotState = SlotState.UNKNOWN
    fill: float | None = None
    confidence: float = 0.0
    last_reason: str = ""
    last_similarity: float = 1.0
    occluded_frames: int = 0
    observations: int = 0


def _bbox(polygon: Polygon) -> tuple[int, int, int, int]:
    points = polygon.pixels
    x1, y1 = points.min(axis=0)
    x2, y2 = points.max(axis=0)
    return int(x1), int(y1), int(max(x1 + 1, x2)), int(max(y1 + 1, y2))


def _overlap_fraction(box_a: tuple[float, float, float, float],
                      box_b: tuple[int, int, int, int]) -> float:
    """Fraction of `box_b` (the slot) covered by `box_a` (a person)."""
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_b = max(1.0, (bx2 - bx1) * (by2 - by1))
    return inter / area_b


class ShelfEngine:
    def __init__(self, shelves, *, store: str = "demo-store", node: str = "pi5-01",
                 cam: str = "shelf", method: str = "reference",
                 embedder: Embedder | None = None, detector=None,
                 reference_dir: str | Path | None = None) -> None:
        """`shelves` is a list of `(shelf_name, [SlotSpec, ...], shelf_config)`."""
        self.store, self.node, self.cam = store, node, cam
        self.method = method
        self.embedder = embedder or Embedder()
        self.detector = detector
        self.reference_dir = Path(reference_dir) if reference_dir else None
        self.shelves: dict[str, dict] = {}
        for name, slots, config in shelves:
            self.shelves[name] = {
                "config": config,
                "slots": {s.polygon.name: _SlotRuntime(spec=s) for s in slots},
            }
            for runtime in self.shelves[name]["slots"].values():
                runtime.votes = deque(maxlen=config.vote_n)
        self.gated_out = 0
        self.evaluated = 0
        if self.reference_dir:
            self.load_references()

    # -- references ------------------------------------------------------- #
    def slot_polygons(self) -> list[Polygon]:
        return [runtime.spec.polygon
                for shelf in self.shelves.values() for runtime in shelf["slots"].values()]

    def capture_references(self, image: np.ndarray, shelf: str | None = None) -> int:
        """The "Restocked" action: this is the only supervision the engine ever
        gets, and it is one button press."""
        count = 0
        for shelf_name, entry in self.shelves.items():
            if shelf and shelf_name != shelf:
                continue
            for runtime in entry["slots"].values():
                crop = self._crop(image, runtime.spec.polygon)
                if crop.size == 0:
                    continue
                runtime.reference_crop = crop.copy()
                runtime.reference_edges = edge_density(crop)
                runtime.reference_colour = colour_mass(crop)
                runtime.reference_embedding = self.embedder(crop)
                runtime.votes.clear()
                runtime.state = SlotState.FULL
                runtime.fill = 1.0
                count += 1
        if self.reference_dir and count:
            self.save_references()
        return count

    def announce(self, clock: Clock, shelf: str | None = None) -> list[Event]:
        """Publish the current state of every slot with a reference.

        Called straight after a restock so the dashboard shows FULL immediately
        rather than UNKNOWN until the next state *change*.  Without this, a slot
        that is correctly full for an hour reports nothing at all, which reads as
        a broken sensor.
        """
        events: list[Event] = []
        for shelf_name, entry in self.shelves.items():
            if shelf and shelf_name != shelf:
                continue
            for slot_name, runtime in entry["slots"].items():
                if runtime.reference_crop is None:
                    continue
                events.append(make_event(
                    ts=clock.now(), store=self.store, node=self.node, cam=self.cam,
                    type=EventType.SLOT_STATE,
                    data=SlotStateData(shelf=shelf_name, slot=slot_name,
                                       sku=runtime.spec.sku, state=runtime.state,
                                       fill=runtime.fill, confidence=1.0,
                                       reason="restocked reference captured")))
        return events

    def save_references(self) -> None:
        """Reference crops are shelf pictures, not people.  They are the one
        thing the privacy rule permits keeping (a calibration snapshot)."""
        if not self.reference_dir:
            return
        self.reference_dir.mkdir(parents=True, exist_ok=True)
        for shelf_name, entry in self.shelves.items():
            for slot_name, runtime in entry["slots"].items():
                if runtime.reference_crop is None:
                    continue
                cv2.imwrite(str(self.reference_dir / f"{shelf_name}__{slot_name}.png"),
                            runtime.reference_crop)

    def load_references(self) -> int:
        if not self.reference_dir or not self.reference_dir.is_dir():
            return 0
        loaded = 0
        for shelf_name, entry in self.shelves.items():
            for slot_name, runtime in entry["slots"].items():
                path = self.reference_dir / f"{shelf_name}__{slot_name}.png"
                if not path.is_file():
                    continue
                crop = cv2.imread(str(path))
                if crop is None:
                    continue
                runtime.reference_crop = crop
                runtime.reference_edges = edge_density(crop)
                runtime.reference_colour = colour_mass(crop)
                runtime.reference_embedding = self.embedder(crop)
                loaded += 1
        return loaded

    # -- measurement ------------------------------------------------------ #
    @staticmethod
    def _crop(image: np.ndarray, polygon: Polygon) -> np.ndarray:
        height, width = image.shape[:2]
        x1, y1, x2, y2 = _bbox(polygon)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(width, x2), min(height, y2)
        if x2 <= x1 or y2 <= y1:
            return np.zeros((0, 0, 3), dtype=np.uint8)
        return image[y1:y2, x1:x2]

    def _fill_from_reference(self, crop: np.ndarray, runtime: _SlotRuntime) -> tuple[float, float]:
        """Returns (fill ratio, confidence)."""
        if runtime.reference_edges is None or runtime.reference_crop is None:
            return 1.0, 0.0
        edges_now = edge_density(crop)
        colour_now = colour_mass(crop)
        edge_ratio = edges_now / max(runtime.reference_edges, 1e-4)
        colour_ratio = colour_now / max(runtime.reference_colour or 1e-4, 1e-4)
        structure = max(0.0, ssim(crop, runtime.reference_crop))
        # Texture carries most of the signal (an emptied slot shows a flat panel);
        # colour mass is a cross-check that fails differently under glare.
        fill = float(np.clip(0.7 * edge_ratio + 0.3 * colour_ratio, 0.0, 1.2))
        # Confidence must describe how sure we are of the *measurement*, not how
        # much the slot still resembles its reference - an emptied slot looks
        # nothing like its reference and we are very sure about it.  So it is the
        # agreement between two estimators that fail in different ways: texture
        # (which glare inflates) and colour mass (which glare washes out).
        agreement = 1.0 - min(1.0, abs(edge_ratio - colour_ratio))
        confidence = float(np.clip(agreement, 0.0, 1.0))
        runtime.last_similarity = structure
        return min(fill, 1.0), confidence

    def _fill_from_detector(self, crop: np.ndarray, runtime: _SlotRuntime) -> tuple[float, float]:
        detections = self.detector.detect(crop)
        products = [d for d in detections if d.cls == 0]
        reference = runtime.spec.reference_facings or max(1, len(products))
        fill = float(np.clip(len(products) / max(1, reference), 0.0, 1.0))
        confidence = float(np.mean([d.conf for d in products])) if products else 0.5
        return fill, confidence

    # -- main loop -------------------------------------------------------- #
    def update(self, image: np.ndarray, tracks: list[Track], clock: Clock) -> list[Event]:
        events: list[Event] = []
        person_boxes = [t.xyxy for t in tracks]

        for shelf_name, entry in self.shelves.items():
            config = entry["config"]
            for slot_name, runtime in entry["slots"].items():
                box = _bbox(runtime.spec.polygon)
                # --- occlusion gate (audit H5) ---
                if any(_overlap_fraction(p, box) > config.occlusion_iou for p in person_boxes):
                    runtime.occluded_frames += 1
                    self.gated_out += 1
                    continue

                crop = self._crop(image, runtime.spec.polygon)
                if crop.size == 0:
                    continue
                if runtime.reference_crop is None:
                    continue  # nothing to compare against until someone restocks

                if self.method == "detector" and self.detector is not None:
                    fill, confidence = self._fill_from_detector(crop, runtime)
                else:
                    fill, confidence = self._fill_from_reference(crop, runtime)

                similarity = cosine(self.embedder(crop), runtime.reference_embedding) \
                    if runtime.reference_embedding is not None else 1.0

                if fill <= config.empty_threshold:
                    observed = SlotState.EMPTY
                    reason = f"fill {fill:.2f} <= empty {config.empty_threshold}"
                elif fill <= config.low_threshold:
                    observed = SlotState.LOW
                    reason = f"fill {fill:.2f} <= low {config.low_threshold}"
                elif similarity < 0.75:
                    # Full, but it does not look like what should be here.
                    observed = SlotState.WRONG_ITEM
                    reason = f"appearance {similarity:.2f} vs reference"
                else:
                    observed = SlotState.FULL
                    reason = f"fill {fill:.2f}"

                runtime.votes.append(observed)
                runtime.fill = round(fill, 3)
                runtime.confidence = round(confidence, 3)
                runtime.last_reason = reason
                runtime.observations += 1
                self.evaluated += 1

                # --- K-of-N voting (audit H6) ---
                agreeing = sum(1 for v in runtime.votes if v is observed)
                if agreeing < config.vote_k or observed is runtime.state:
                    continue
                runtime.state = observed
                events.append(make_event(
                    ts=clock.now(), store=self.store, node=self.node, cam=self.cam,
                    type=EventType.SLOT_STATE,
                    data=SlotStateData(shelf=shelf_name, slot=slot_name,
                                       sku=runtime.spec.sku, state=observed,
                                       fill=runtime.fill, confidence=runtime.confidence,
                                       reason=reason),
                ))
        return events

    def summary(self) -> dict:
        return {
            shelf_name: {
                "slots": {
                    slot_name: {
                        "sku": runtime.spec.sku,
                        "state": runtime.state.value,
                        "fill": runtime.fill,
                        "confidence": runtime.confidence,
                        "similarity_to_reference": round(runtime.last_similarity, 3),
                        "observations": runtime.observations,
                        "occluded_frames": runtime.occluded_frames,
                        "has_reference": runtime.reference_crop is not None,
                    }
                    for slot_name, runtime in entry["slots"].items()
                }
            }
            for shelf_name, entry in self.shelves.items()
        }

    def slot_price(self, shelf: str, slot: str) -> tuple[str | None, float | None]:
        entry = self.shelves.get(shelf)
        if not entry:
            return None, None
        runtime = entry["slots"].get(slot)
        if not runtime:
            return None, None
        return runtime.spec.sku, runtime.spec.price
