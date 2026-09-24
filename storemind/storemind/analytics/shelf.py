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
        texture falls.  `fill = texture(now) / texture(reference)`, cross-checked
        against colour mass and (v2) gradient structural similarity.
    *   **planogram check** - a slot that is full but does not *look* like its
        reference holds the wrong product.
    *   **K-of-N voting** - state changes only when K of the last N gated
        observations agree.  Audit H6: the old code re-alerted every 10 seconds.

Shelf v2 (M3, research/23 section 3.3, docs/SHELF.md) makes this survive real
store lighting.  Every step can be switched off in `ShelfConfig`, and the v1
values reproduce the old engine exactly (`eval/eval_shelf_lighting.py` compares
the two):

*   **white balance** (gray world on the frame) removes the warm/cool tint of
    evening sun and tube lights, which otherwise looks like a different product;
*   **gain-normalised texture**: gradient magnitude divided by local brightness,
    so halving the light does not halve the "fill" (v1's fixed-threshold Canny
    did exactly that);
*   **CLAHE** on luminance and a **glare mask** (saturated, colourless pixels are
    ignored by every measure);
*   **gradient SSIM** joins the fill estimate instead of only being displayed;
*   a **reference bank** per slot: one reference per lighting condition, chosen
    by BH1750 lux when a light sensor is fitted, else by frame brightness.  New
    lighting is learnt while the slot is confidently FULL; a slow **drift**
    update follows gradual change;
*   **too dark -> UNKNOWN**, never EMPTY; a **sudden light change** skips one
    cycle instead of voting on a half-adapted camera;
*   **rectification**: 4-point slots are warped to a rectangle so oblique CCTV
    views compare fairly;
*   **weight fusion** with the load cell under the slot (weight wins on deep
    shelves; disagreement asks for a "check shelf" with both numbers).

`method="detector"` swaps the fill estimate for a class-agnostic product/gap
detector (SKU-110K + gap datasets, trained by
`storemind/train/shelf_detector_kaggle.ipynb`) counting facings per slot.  The
state machine, gating and voting are identical either way.
"""

from __future__ import annotations

import logging
import math
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

def _gray(crop: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop


def edge_density(crop: np.ndarray, low: int = 60, high: int = 160,
                 mask: np.ndarray | None = None) -> float:
    """Fraction of pixels on an edge (v1 measure).  Packets have printed faces and
    box edges; an empty slot shows a flat back panel."""
    if crop.size == 0:
        return 0.0
    gray = cv2.GaussianBlur(_gray(crop), (3, 3), 0)
    edges = cv2.Canny(gray, low, high)
    if mask is not None:
        valid = ~mask
        return float(np.count_nonzero(edges[valid])) / max(1, int(valid.sum()))
    return float(np.count_nonzero(edges)) / float(edges.size)


def auto_canny_thresholds(crop: np.ndarray, sigma: float = 0.33) -> tuple[int, int]:
    """Median-based Canny thresholds (the usual 'auto Canny')."""
    median = float(np.median(_gray(crop)))
    return int(max(0, (1.0 - sigma) * median)), int(min(255, (1.0 + sigma) * median))


# Chosen on the synthetic tuning seeds (eval/eval_shelf_lighting.py --tune).
TEXTURE_THRESHOLD = 0.5
TEXTURE_BLUR = 5


def gradient_texture(crop: np.ndarray, mask: np.ndarray | None = None,
                     threshold: float | None = None) -> float:
    """Fraction of pixels whose gradient is strong *relative to local brightness*.

    Multiplying the image by a light gain k multiplies both the gradient and the
    local mean by k, so the ratio - and this density - does not change.  That is
    the property v1's fixed-threshold Canny lacked.
    """
    if crop.size == 0:
        return 0.0
    threshold = TEXTURE_THRESHOLD if threshold is None else threshold
    gray = cv2.GaussianBlur(_gray(crop).astype(np.float32), (TEXTURE_BLUR, TEXTURE_BLUR), 0)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    local = cv2.blur(gray, (15, 15)) + 8.0
    strong = (np.hypot(gx, gy) / local) > threshold
    if mask is not None:
        valid = ~mask
        return float(np.count_nonzero(strong[valid])) / max(1, int(valid.sum()))
    return float(np.count_nonzero(strong)) / float(strong.size)


def colour_mass(crop: np.ndarray, mask: np.ndarray | None = None) -> float:
    """Mean saturation.  Product packaging is colourful; shelf liners are not."""
    if crop.size == 0:
        return 0.0
    saturation = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[..., 1]
    if mask is not None and (~mask).any():
        return float(saturation[~mask].mean()) / 255.0
    return float(saturation.mean()) / 255.0


def ssim(a: np.ndarray, b: np.ndarray, size: int = 64) -> float:
    """Global structural similarity on grayscale.  Implemented here so the Pi
    image does not need scikit-image."""
    if a.size == 0 or b.size == 0:
        return 0.0
    ga = cv2.resize(_gray(a), (size, size)).astype(np.float64)
    gb = cv2.resize(_gray(b), (size, size)).astype(np.float64)
    return _ssim_arrays(ga, gb, (0.01 * 255) ** 2, (0.03 * 255) ** 2)


def _ssim_arrays(ga: np.ndarray, gb: np.ndarray, c1: float, c2: float) -> float:
    mu_a, mu_b = ga.mean(), gb.mean()
    va, vb = ga.var(), gb.var()
    cov = ((ga - mu_a) * (gb - mu_b)).mean()
    numerator = (2 * mu_a * mu_b + c1) * (2 * cov + c2)
    denominator = (mu_a ** 2 + mu_b ** 2 + c1) * (va + vb + c2)
    return float(numerator / denominator) if denominator > 1e-12 else 0.0


def gradient_map(crop: np.ndarray, mask: np.ndarray | None = None, size: int = 64) -> np.ndarray:
    """Brightness-normalised gradient magnitude, resized - the input to gradient SSIM."""
    gray = cv2.GaussianBlur(_gray(crop).astype(np.float32), (3, 3), 0)
    magnitude = np.hypot(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3),
                         cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    magnitude = magnitude / (cv2.blur(gray, (15, 15)) + 8.0)
    if mask is not None:
        magnitude[mask] = 0.0
    return cv2.resize(magnitude, (size, size), interpolation=cv2.INTER_AREA).astype(np.float64)


def gradient_ssim(a_map: np.ndarray, b_map: np.ndarray) -> float:
    """SSIM between two gradient maps: structure, not colour or brightness."""
    return max(0.0, _ssim_arrays(a_map, b_map, 1e-4, 9e-4))


def glare_mask(crop: np.ndarray, v_min: int = 245, s_max: int = 40) -> np.ndarray:
    """Specular highlights: nearly white and colourless.  Dilated a little."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = ((hsv[..., 2] >= v_min) & (hsv[..., 1] <= s_max)).astype(np.uint8)
    return cv2.dilate(mask, np.ones((5, 5), np.uint8)).astype(bool)


def gray_world(image: np.ndarray, exclude: np.ndarray | None = None) -> np.ndarray:
    """Scale each channel so the average colour is gray.

    `exclude` masks out the slots: the wall and shelf boards do not change when
    stock moves, so the white balance does not shift when a slot empties."""
    pixels = image[~exclude] if exclude is not None and (~exclude).any() else image.reshape(-1, 3)
    means = pixels.reshape(-1, 3).mean(axis=0) + 1e-6
    gains = means.mean() / means
    return np.clip(image.astype(np.float32) * gains, 0, 255).astype(np.uint8)


def apply_clahe(crop: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    lab[..., 0] = clahe.apply(lab[..., 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def frame_brightness(image: np.ndarray) -> float:
    """Mean HSV value, 0..1 - the light level when there is no BH1750."""
    return float(cv2.cvtColor(image, cv2.COLOR_BGR2HSV)[..., 2].mean()) / 255.0


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
    full_grams: float | None = None
    deep: bool = False


@dataclass
class _Reference:
    """One known-good picture of a full slot under one lighting condition."""

    crop: np.ndarray                  # processed crop (white balance/CLAHE as configured)
    texture: float
    colour: float
    embedding: np.ndarray
    grad: np.ndarray | None
    level: float | None               # lux, or frame brightness, when it was taken


@dataclass
class _SlotRuntime:
    spec: SlotSpec
    bank: list[_Reference] = field(default_factory=list)
    votes: deque = field(default_factory=lambda: deque(maxlen=5))
    state: SlotState = SlotState.UNKNOWN
    fill: float | None = None
    confidence: float = 0.0
    last_reason: str = ""
    last_similarity: float = 1.0
    occluded_frames: int = 0
    occluded_run: int = 0
    observations: int = 0
    weight_g: float | None = None
    full_grams: float | None = None

    @property
    def reference_crop(self) -> np.ndarray | None:
        """The restock reference (first in the bank) - kept for older callers."""
        return self.bank[0].crop if self.bank else None


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
                 reference_dir: str | Path | None = None, embed_threshold: float = 0.75) -> None:
        """`shelves` is a list of `(shelf_name, [SlotSpec, ...], shelf_config)`."""
        self.store, self.node, self.cam = store, node, cam
        self.method = method
        self.embedder = embedder or Embedder()
        self.detector = detector
        self.embed_threshold = embed_threshold
        self.reference_dir = Path(reference_dir) if reference_dir else None
        self.shelves: dict[str, dict] = {}
        for name, slots, config in shelves:
            self.shelves[name] = {
                "config": config,
                "slots": {s.polygon.name: _SlotRuntime(spec=s, full_grams=s.full_grams)
                          for s in slots},
                "last_level": None,
            }
            for runtime in self.shelves[name]["slots"].values():
                runtime.votes = deque(maxlen=config.vote_n)
        self.gated_out = 0
        self.evaluated = 0
        self.camera_ok = True
        self._lux: dict[str | None, float] = {}
        if self.reference_dir:
            self.load_references()

    # -- sensor inputs ------------------------------------------------------ #
    def observe_lux(self, lux: float | None, node: str | None = None) -> None:
        """BH1750 reading (ENVIRONMENT.lux).  `node` is matched to `lux_node`."""
        if lux is None:
            return
        self._lux[node] = float(lux)
        self._lux[None] = float(lux)          # "any node" = the latest reading

    def observe_weight(self, shelf: str, slot: str, grams: float, stable: bool = True) -> None:
        """Load cell under a slot.  Unstable readings (shelf being handled) are ignored."""
        runtime = self.shelves.get(shelf, {}).get("slots", {}).get(slot)
        if runtime is not None and stable:
            runtime.weight_g = float(grams)

    def set_camera_ok(self, ok: bool) -> None:
        self.camera_ok = ok

    def _lux_for(self, config) -> float | None:
        return self._lux.get(getattr(config, "lux_node", None))

    # -- references ------------------------------------------------------- #
    def slot_polygons(self) -> list[Polygon]:
        return [runtime.spec.polygon
                for shelf in self.shelves.values() for runtime in shelf["slots"].values()]

    def _prepare(self, image: np.ndarray, config, entry: dict | None = None) -> np.ndarray:
        if not getattr(config, "white_balance", False):
            return image
        mask = None
        if entry is not None:
            mask = entry.get("_slot_mask")
            if mask is None or mask.shape != image.shape[:2]:
                mask = np.zeros(image.shape[:2], np.uint8)
                for runtime in entry["slots"].values():
                    cv2.fillPoly(mask, [runtime.spec.polygon.pixels.astype(np.int32)], 1)
                mask = mask.astype(bool)
                entry["_slot_mask"] = mask
        return gray_world(image, mask)

    def _level(self, image: np.ndarray, config) -> tuple[float | None, str]:
        lux = self._lux_for(config)
        if lux is not None:
            return lux, "lux"
        return frame_brightness(image), "brightness"

    def _make_reference(self, crop_raw: np.ndarray, config, level: float | None) -> _Reference:
        processed, mask = self._process(crop_raw, config)
        return _Reference(crop=processed, texture=self._texture(processed, mask, config),
                          colour=colour_mass(processed, mask), embedding=self.embedder(crop_raw),
                          grad=gradient_map(processed, mask) if getattr(config, "use_ssim", False)
                          else None, level=level)

    def capture_references(self, image: np.ndarray, shelf: str | None = None) -> int:
        """The "Restocked" action: this is the only supervision the engine ever
        gets, and it is one button press.  It resets the bank to this lighting."""
        count = 0
        for shelf_name, entry in self.shelves.items():
            if shelf and shelf_name != shelf:
                continue
            config = entry["config"]
            prepared = self._prepare(image, config, entry)
            level, _source = self._level(image, config)
            entry["last_level"] = level
            for runtime in entry["slots"].values():
                crop = self._crop(prepared, runtime.spec.polygon, config)
                if crop.size == 0:
                    continue
                runtime.bank = [self._make_reference(crop, config, level)]
                runtime.votes.clear()
                runtime.state = SlotState.FULL
                runtime.fill = 1.0
                runtime.occluded_run = 0
                if runtime.spec.full_grams is None and runtime.weight_g is not None:
                    runtime.full_grams = runtime.weight_g     # learnt at restock
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
                if not runtime.bank:
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
                if runtime.bank:
                    cv2.imwrite(str(self.reference_dir / f"{shelf_name}__{slot_name}.png"),
                                runtime.bank[0].crop)

    def load_references(self) -> int:
        """Loads the restock reference (the bank re-learns other lighting)."""
        if not self.reference_dir or not self.reference_dir.is_dir():
            return 0
        loaded = 0
        for shelf_name, entry in self.shelves.items():
            config = entry["config"]
            for slot_name, runtime in entry["slots"].items():
                path = self.reference_dir / f"{shelf_name}__{slot_name}.png"
                crop = cv2.imread(str(path)) if path.is_file() else None
                if crop is None:
                    continue
                mask = self._glare(crop, config)
                runtime.bank = [_Reference(crop=crop, texture=self._texture(crop, mask, config),
                                           colour=colour_mass(crop, mask), embedding=self.embedder(crop),
                                           grad=gradient_map(crop, mask)
                                           if getattr(config, "use_ssim", False) else None,
                                           level=None)]
                loaded += 1
        return loaded

    # -- measurement ------------------------------------------------------ #
    @staticmethod
    def _crop(image: np.ndarray, polygon: Polygon, config=None) -> np.ndarray:
        points = polygon.pixels
        if config is not None and getattr(config, "rectify", False) and len(points) == 4:
            width, height = config.rectify_size
            destination = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1],
                                    [0, height - 1]], dtype=np.float32)
            matrix = cv2.getPerspectiveTransform(points.astype(np.float32), destination)
            return cv2.warpPerspective(image, matrix, (width, height))
        height, width = image.shape[:2]
        x1, y1, x2, y2 = _bbox(polygon)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(width, x2), min(height, y2)
        if x2 <= x1 or y2 <= y1:
            return np.zeros((0, 0, 3), dtype=np.uint8)
        return image[y1:y2, x1:x2]

    @staticmethod
    def _glare(crop: np.ndarray, config) -> np.ndarray | None:
        if not getattr(config, "glare_mask", False) or crop.size == 0:
            return None
        return glare_mask(crop, config.glare_v, config.glare_s)

    def _process(self, crop: np.ndarray, config) -> tuple[np.ndarray, np.ndarray | None]:
        """Glare is found on the raw crop (CLAHE would change it), then CLAHE."""
        mask = self._glare(crop, config)
        if getattr(config, "clahe", False) and crop.size:
            crop = apply_clahe(crop)
        return crop, mask

    @staticmethod
    def _texture(crop: np.ndarray, mask: np.ndarray | None, config) -> float:
        if getattr(config, "texture", "canny") == "gradient":
            return gradient_texture(crop, mask)
        if getattr(config, "canny", "fixed") == "auto":
            low, high = auto_canny_thresholds(crop)
        else:
            low, high = getattr(config, "canny_low", 60), getattr(config, "canny_high", 160)
        return edge_density(crop, low, high, mask)

    @staticmethod
    def _nearest(bank: list[_Reference], level: float | None) -> _Reference:
        if level is None or len(bank) == 1:
            return bank[0]
        known = [r for r in bank if r.level is not None]
        if not known:
            return bank[0]
        return min(known, key=lambda r: abs(math.log(max(level, 1e-3)) - math.log(max(r.level, 1e-3))))

    def _fill_from_reference(self, crop: np.ndarray, mask: np.ndarray | None,
                             reference: _Reference, runtime: _SlotRuntime, config) -> tuple[float, float]:
        """Returns (fill ratio, confidence)."""
        texture_now = self._texture(crop, mask, config)
        colour_now = colour_mass(crop, mask)
        texture_ratio = texture_now / max(reference.texture, 1e-4)
        colour_ratio = colour_now / max(reference.colour or 1e-4, 1e-4)
        # Texture carries most of the signal (an emptied slot shows a flat panel);
        # colour mass is a cross-check that fails differently under glare.
        fill = 0.7 * texture_ratio + 0.3 * colour_ratio
        estimates = [texture_ratio, colour_ratio]
        if getattr(config, "use_ssim", False) and reference.grad is not None:
            structure = gradient_ssim(gradient_map(crop, mask), reference.grad)
            weight = config.ssim_weight
            fill = (1.0 - weight) * fill + weight * structure
            estimates.append(structure)
            runtime.last_similarity = structure
        else:
            runtime.last_similarity = max(0.0, ssim(crop, reference.crop))
        fill = float(np.clip(fill, 0.0, 1.2))
        # Confidence describes how sure we are of the *measurement*: the agreement
        # between estimators that fail in different ways (glare inflates texture
        # and washes out colour).
        # Each estimate is clipped to [0, 1] first: "more texture than the
        # reference" (CLAHE on a dimmer frame does that) is not disagreement
        # about how full the slot is.
        clipped = [min(1.0, max(0.0, e)) for e in estimates]
        agreement = 1.0 - (max(clipped) - min(clipped))
        return min(fill, 1.0), float(np.clip(agreement, 0.0, 1.0))

    def _fill_from_detector(self, crop: np.ndarray, runtime: _SlotRuntime) -> tuple[float, float]:
        detections = self.detector.detect(crop)
        products = [d for d in detections if d.cls == 0]
        reference = runtime.spec.reference_facings or max(1, len(products))
        fill = float(np.clip(len(products) / max(1, reference), 0.0, 1.0))
        confidence = float(np.mean([d.conf for d in products])) if products else 0.5
        return fill, confidence

    def _fuse_weight(self, fill: float, runtime: _SlotRuntime, config) -> tuple[float, str]:
        """Camera fill + load-cell fill.  Returns (fill, extra reason)."""
        if getattr(config, "weight_mode", "camera") != "fuse" or runtime.weight_g is None \
                or not runtime.full_grams:
            return fill, ""
        weight_fill = float(np.clip(runtime.weight_g / runtime.full_grams, 0.0, 1.0))
        note = f"; weight fill {weight_fill:.2f} ({runtime.weight_g:.0f} g)"
        if abs(fill - weight_fill) > config.disagree_fill:
            note += f"; CHECK SHELF: camera {fill:.2f} vs weight {weight_fill:.2f}"
        fused = weight_fill if runtime.spec.deep else 0.5 * (fill + weight_fill)
        return fused, note

    # -- main loop -------------------------------------------------------- #
    def _set_unknown(self, shelf_name: str, entry: dict, reason: str, clock: Clock) -> list[Event]:
        events = []
        for slot_name, runtime in entry["slots"].items():
            if not runtime.bank or runtime.state is SlotState.UNKNOWN:
                continue
            runtime.state = SlotState.UNKNOWN
            runtime.last_reason = reason
            events.append(self._state_event(shelf_name, slot_name, runtime, reason, clock))
        return events

    def _state_event(self, shelf_name: str, slot_name: str, runtime: _SlotRuntime,
                     reason: str, clock: Clock) -> Event:
        return make_event(
            ts=clock.now(), store=self.store, node=self.node, cam=self.cam,
            type=EventType.SLOT_STATE,
            data=SlotStateData(shelf=shelf_name, slot=slot_name, sku=runtime.spec.sku,
                               state=runtime.state, fill=runtime.fill,
                               confidence=runtime.confidence, reason=reason))

    def update(self, image: np.ndarray, tracks: list[Track], clock: Clock) -> list[Event]:
        events: list[Event] = []
        person_boxes = [t.xyxy for t in tracks]

        for shelf_name, entry in self.shelves.items():
            config = entry["config"]
            if not self.camera_ok:
                events += self._set_unknown(shelf_name, entry, "camera fault", clock)
                continue
            level, source = self._level(image, config)
            dark_limit = config.dark_lux if source == "lux" else config.dark_brightness
            if dark_limit is not None and level is not None and level < dark_limit:
                events += self._set_unknown(
                    shelf_name, entry, f"too dark ({source} {level:.2f} < {dark_limit})", clock)
                entry["last_level"] = level
                continue
            previous, entry["last_level"] = entry["last_level"], level
            jump = config.lux_jump_ratio
            if jump and previous and level and max(previous, level) / max(min(previous, level), 1e-6) > jump:
                continue      # the camera's exposure is still adapting: skip this cycle

            prepared = self._prepare(image, config, entry)
            for slot_name, runtime in entry["slots"].items():
                box = _bbox(runtime.spec.polygon)
                # --- occlusion gate (audit H5) ---
                if any(_overlap_fraction(p, box) > config.occlusion_iou for p in person_boxes):
                    runtime.occluded_frames += 1
                    runtime.occluded_run += 1
                    self.gated_out += 1
                    if runtime.occluded_run == config.occluded_unknown_cycles and runtime.bank \
                            and runtime.state is not SlotState.UNKNOWN:
                        runtime.state = SlotState.UNKNOWN
                        events.append(self._state_event(shelf_name, slot_name, runtime,
                                                        "occluded for a long time", clock))
                    continue
                runtime.occluded_run = 0
                if not runtime.bank:
                    continue  # nothing to compare against until someone restocks
                raw = self._crop(prepared, runtime.spec.polygon, config)
                if raw.size == 0:
                    continue
                crop, mask = self._process(raw, config)
                reference = self._nearest(runtime.bank, level)

                if self.method == "detector" and self.detector is not None:
                    fill, confidence = self._fill_from_detector(crop, runtime)
                else:
                    fill, confidence = self._fill_from_reference(crop, mask, reference, runtime, config)
                fill, weight_note = self._fuse_weight(fill, runtime, config)
                appearance = self.embedder(raw)       # colour histogram before CLAHE
                similarity = cosine(appearance, reference.embedding)

                if fill <= config.empty_threshold:
                    observed = SlotState.EMPTY
                    reason = f"fill {fill:.2f} <= empty {config.empty_threshold}"
                elif fill <= config.low_threshold:
                    observed = SlotState.LOW
                    reason = f"fill {fill:.2f} <= low {config.low_threshold}"
                elif similarity < self.embed_threshold:
                    # Full, but it does not look like what should be here.
                    observed = SlotState.WRONG_ITEM
                    reason = f"appearance {similarity:.2f} vs reference"
                else:
                    observed = SlotState.FULL
                    reason = f"fill {fill:.2f}"
                reason += weight_note
                if len(runtime.bank) > 1 or level is not None:
                    reason += f"; {source} {level:.2f}" if level is not None else ""

                runtime.votes.append(observed)
                runtime.fill = round(fill, 3)
                runtime.confidence = round(confidence, 3)
                runtime.last_reason = reason
                runtime.observations += 1
                self.evaluated += 1
                self._learn(runtime, observed, confidence, crop, mask, reference, level, config,
                            appearance)

                # --- K-of-N voting (audit H6) ---
                agreeing = sum(1 for v in runtime.votes if v is observed)
                if agreeing < config.vote_k or observed is runtime.state:
                    continue
                runtime.state = observed
                events.append(self._state_event(shelf_name, slot_name, runtime, reason, clock))
        return events

    def _learn(self, runtime: _SlotRuntime, observed: SlotState, confidence: float,
               crop: np.ndarray, mask: np.ndarray | None, reference: _Reference,
               level: float | None, config, appearance: np.ndarray) -> None:
        """Grow the reference bank for new lighting; follow slow drift."""
        if observed is not SlotState.FULL or runtime.state is not SlotState.FULL:
            return
        if (config.reference_bank > len(runtime.bank) and level is not None and confidence >= 0.6
                and all(r.level is None or max(level, r.level) / max(min(level, r.level), 1e-6)
                        > config.bank_lux_ratio for r in runtime.bank)):
            runtime.bank.append(_Reference(
                crop=crop.copy(), texture=self._texture(crop, mask, config),
                colour=colour_mass(crop, mask), embedding=appearance,
                grad=gradient_map(crop, mask) if config.use_ssim else None, level=level))
            return
        alpha = config.drift_alpha
        if alpha > 0 and confidence >= 0.7 and reference.crop.shape == crop.shape:
            blended = cv2.addWeighted(reference.crop, 1.0 - alpha, crop, alpha, 0.0)
            reference.crop = blended
            reference.texture = self._texture(blended, mask, config)
            reference.colour = colour_mass(blended, mask)
            reference.embedding = (1.0 - alpha) * reference.embedding + alpha * appearance
            if reference.grad is not None:
                reference.grad = gradient_map(blended, mask)

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
                        "has_reference": bool(runtime.bank),
                        "references": len(runtime.bank),
                        "weight_g": runtime.weight_g,
                        "reason": runtime.last_reason,
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
