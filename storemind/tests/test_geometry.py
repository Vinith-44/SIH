"""Geometry: foot point, hysteresis band, polygons, homography."""

from __future__ import annotations

import numpy as np
import pytest

from storemind.core.geometry import (
    Line,
    Polygon,
    apply_homography,
    foot_point,
    homography_from_points,
    to_pixels,
)


def test_foot_point_is_bottom_centre():
    assert foot_point((10.0, 20.0, 30.0, 60.0)) == (20.0, 60.0)


def test_to_pixels_detects_normalised_coordinates():
    normalised = to_pixels([(0.0, 0.0), (1.0, 0.5)], 640, 480)
    assert normalised.tolist() == [[0.0, 0.0], [640.0, 240.0]]
    # Already-pixel coordinates must be left alone.
    pixels = to_pixels([(0.0, 0.0), (640.0, 240.0)], 640, 480)
    assert pixels.tolist() == [[0.0, 0.0], [640.0, 240.0]]


def test_line_side_and_dead_band():
    line = Line("door", (0.0, 0.5), (1.0, 0.5), margin_px=10).resolve(640, 480)
    assert line.side((320, 300)) == 1      # below the line
    assert line.side((320, 140)) == -1     # above the line
    assert line.side((320, 242)) == 0      # inside the +/-10 px dead band
    assert line.side((320, 238)) == 0


def test_line_signed_distance_scales_with_pixels():
    line = Line("door", (0.0, 0.5), (1.0, 0.5), margin_px=5).resolve(640, 480)
    assert line.signed_distance((0, 240)) == pytest.approx(0.0, abs=1e-6)
    assert line.signed_distance((0, 260)) == pytest.approx(20.0, abs=1e-6)


def test_polygon_contains():
    polygon = Polygon("lane", [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)]).resolve(100, 100)
    assert polygon.contains((50, 50))
    assert not polygon.contains((10, 50))
    assert not polygon.contains((50, 95))


def test_polygon_handles_concave_shape():
    # An L-shaped lane: the notch must NOT be inside.
    points = [(0, 0), (100, 0), (100, 40), (40, 40), (40, 100), (0, 100)]
    polygon = Polygon("L", points).resolve(100, 100)
    assert polygon.contains((20, 20))
    assert polygon.contains((80, 20))
    assert not polygon.contains((80, 80))


def test_homography_maps_the_four_reference_points():
    src = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    dst = [(0.0, 0.0), (5.0, 0.0), (5.0, 4.0), (0.0, 4.0)]
    h = homography_from_points(src, dst)
    for source, target in zip(src, dst):
        mapped = apply_homography(h, source)
        assert mapped[0] == pytest.approx(target[0], abs=1e-6)
        assert mapped[1] == pytest.approx(target[1], abs=1e-6)


def test_homography_handles_perspective():
    """A trapezoid in the image (a floor seen at an angle) must flatten to a
    rectangle on the plan - that is the whole point of the heatmap homography."""
    src = [(100.0, 200.0), (540.0, 200.0), (640.0, 480.0), (0.0, 480.0)]
    dst = [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)]
    h = homography_from_points(src, dst)
    centre_image = (320.0, 340.0)
    x, y = apply_homography(h, centre_image)
    assert 0.0 < x < 4.0 and 0.0 < y < 3.0
    # Symmetry: the image is left-right symmetric about x=320, so should map to x=2.
    assert x == pytest.approx(2.0, abs=1e-6)


def test_homography_needs_four_points():
    with pytest.raises(ValueError):
        homography_from_points([(0, 0), (1, 0), (1, 1)], [(0, 0), (1, 0), (1, 1)])
