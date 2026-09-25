"""Contract for promotion analytics: the promo keys on `cameras[].zones[]` and the
PROMO_STATE event (docs/INTERFACES.md, docs/CONFIG_REFERENCE.md)."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from storemind.core.bus import qos_for, retain_for
from storemind.core.config import ZoneConfig, save_config, load_config, StoreMindConfig
from storemind.core.events import EventType, PromoStateData

SQUARE = [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]]


def promo(**kw) -> ZoneConfig:
    return ZoneConfig(name="endcap", points=SQUARE, kind="promo", **kw)


def test_a_full_promo_zone_loads():
    zone = promo(promo_name="Diwali offer", sku=["SKU-1", "SKU-2"], offer_text="Buy 2 get 1 free",
                 price=49.0, start_date="2026-10-15", end_date="2026-11-05", shelf="endcap", slot="E1",
                 approach_band=0.1, report_every_s=120)
    assert zone.start_date == date(2026, 10, 15)
    assert zone.sku == ["SKU-1", "SKU-2"]


def test_promo_keys_on_another_kind_are_refused():
    with pytest.raises(ValidationError, match="only apply to kind: promo"):
        ZoneConfig(name="aisle", points=SQUARE, kind="zone", offer_text="10% off")


def test_end_before_start_is_refused():
    with pytest.raises(ValidationError, match="before start_date"):
        promo(start_date="2026-11-05", end_date="2026-10-15")


def test_linked_slot_needs_shelf_and_slot():
    with pytest.raises(ValidationError, match="both shelf and slot"):
        promo(shelf="endcap")


@pytest.mark.parametrize("kw", [{"approach_band": 1.5}, {"approach_band": -0.1}, {"report_every_s": 0}])
def test_bad_band_or_window_is_refused(kw):
    with pytest.raises(ValidationError, match="approach_band must be 0-1"):
        promo(**kw)


def test_a_saved_config_with_plain_zones_loads_again(tmp_path):
    """save_config writes every key, promo keys as null on plain zones: that must still load."""
    config = StoreMindConfig(cameras=[{"name": "aisle", "source": "0",
                                       "zones": [{"name": "aisle", "points": SQUARE},
                                                 {"name": "endcap", "points": SQUARE, "kind": "promo",
                                                  "start_date": "2026-10-15"}]}])
    path = tmp_path / "saved.yaml"
    save_config(config, path)
    again = load_config(path)
    assert again.cameras[0].zones[1].start_date == date(2026, 10, 15)


def test_promo_state_is_qos1_and_retained():
    assert qos_for(EventType.PROMO_STATE) == 1
    assert retain_for(EventType.PROMO_STATE)


def test_an_inactive_window_carries_no_counts():
    data = PromoStateData(promo="Diwali offer", zone="endcap", active=False, window_s=300.0)
    assert data.passers_by is None and data.stop_rate is None and data.picks is None
