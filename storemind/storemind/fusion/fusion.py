"""Camera + weight + ToF fusion, and the rupee metric.

Novelty pillars N3 and N4.  The camera sees only the **front** facing of a shelf;
a load cell does not care what the front row looks like.  Each rule below exists
because one sensor alone is ambiguous:

| Rule | Camera says | Sensor says | Conclusion |
|---|---|---|---|
| Confirmed OOS | EMPTY | weight at/near tare | out of stock, high confidence |
| Hidden depletion | FULL | weight falling steadily | back of the shelf is empty |
| Pickup | (anything) | ToF interaction + weight drop | a shopper took an item |
| Shrink flag | no shopper near the shelf | weight drops | unexplained loss |
| Lost sale | shopper dwelling in front of an EMPTY/LOW slot | - | rupees at risk |

`LOST_SALE_RISK` is the number that turns computer vision into a business case:
*"Atta 5 kg was empty for 2 h 10 m at peak; 14 shoppers checked it; about
Rs 4,900 at risk."*  It is an **estimate of risk**, not measured lost revenue, and
the dashboard and PPT must both say so.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

from ..core.clock import Clock
from ..core.events import (
    Event,
    EventType,
    LostSaleRiskData,
    PickupData,
    ShrinkFlagData,
    SlotState,
    make_event,
)

log = logging.getLogger(__name__)


@dataclass
class _Cell:
    """Load-cell history for one shelf slot."""

    samples: deque = field(default_factory=lambda: deque(maxlen=120))  # (t, grams)
    tare_g: float = 0.0
    last_tof_mm: float | None = None
    last_tof_s: float = -1e9
    last_pickup_s: float = -1e9

    def add(self, t: float, grams: float) -> None:
        self.samples.append((t, grams))

    @property
    def latest(self) -> float | None:
        return self.samples[-1][1] if self.samples else None

    def drop_over(self, now_s: float, window_s: float) -> float:
        """Grams lost over the window (positive means weight went down)."""
        window = [(t, g) for t, g in self.samples if now_s - t <= window_s]
        if len(window) < 2:
            return 0.0
        return float(window[0][1] - window[-1][1])


@dataclass
class _SlotView:
    state: SlotState = SlotState.UNKNOWN
    since_s: float = 0.0
    sku: str | None = None
    price: float | None = None
    shoppers_checked: int = 0
    risk_inr: float = 0.0


class FusionEngine:
    def __init__(self, *, store: str = "demo-store", node: str = "pi5-01",
                 cell_map: dict[str, str] | None = None,
                 dwell_lost_sale_s: float = 8.0,
                 hidden_depletion_g: float = 150.0,
                 hidden_depletion_window_s: float = 300.0,
                 pickup_g: float = 20.0,
                 tof_window_s: float = 5.0,
                 shrink_g: float = 50.0,
                 person_near_window_s: float = 20.0) -> None:
        self.store, self.node = store, node
        # config maps "shelf/slot" -> load-cell channel id
        self.cell_map = cell_map or {}
        self._reverse_map = {v: k for k, v in self.cell_map.items()}
        self.dwell_lost_sale_s = dwell_lost_sale_s
        self.hidden_depletion_g = hidden_depletion_g
        self.hidden_depletion_window_s = hidden_depletion_window_s
        self.pickup_g = pickup_g
        self.tof_window_s = tof_window_s
        self.shrink_g = shrink_g
        self.person_near_window_s = person_near_window_s

        self.cells: dict[str, _Cell] = {}
        self.slots: dict[str, _SlotView] = {}
        # zone name -> "shelf/slot" it fronts, filled from config by the pipeline
        self.zone_to_slot: dict[str, str] = {}
        self.last_person_near_s: dict[str, float] = {}
        self.total_risk_inr = 0.0
        self.findings: list[dict] = []
        self._pending: list[Event] = []

    # ------------------------------------------------------------------ #
    def register_zone(self, zone: str, shelf: str, slot: str,
                      sku: str | None = None, price: float | None = None) -> None:
        key = f"{shelf}/{slot}"
        self.zone_to_slot[zone] = key
        view = self.slots.setdefault(key, _SlotView())
        view.sku = sku or view.sku
        view.price = price if price is not None else view.price

    def _cell_for(self, shelf: str, slot: str) -> _Cell | None:
        channel = self.cell_map.get(f"{shelf}/{slot}")
        return self.cells.get(channel) if channel else None

    # ------------------------------------------------------------------ #
    def on_event(self, event: Event, clock: Clock) -> list[Event]:
        if event.type is EventType.SLOT_STATE:
            return self._on_slot_state(event, clock)
        if event.type is EventType.SENSOR:
            return self._on_sensor(event, clock)
        if event.type is EventType.ZONE_VISIT:
            return self._on_zone_visit(event, clock)
        return []

    def _on_slot_state(self, event: Event, clock: Clock) -> list[Event]:
        now = clock.monotonic_s()
        shelf, slot = event.data["shelf"], event.data["slot"]
        key = f"{shelf}/{slot}"
        view = self.slots.setdefault(key, _SlotView())
        view.state = SlotState(event.data["state"])
        view.since_s = now
        if event.data.get("sku"):
            view.sku = event.data["sku"]

        cell = self._cell_for(shelf, slot)
        if cell is not None and cell.latest is not None:
            if view.state is SlotState.EMPTY and cell.latest - cell.tare_g < self.pickup_g:
                self.findings.append({"type": "CONFIRMED_OOS", "slot": key,
                                      "grams": cell.latest, "t": now})
        return []

    def _on_sensor(self, event: Event, clock: Clock) -> list[Event]:
        now = clock.monotonic_s()
        data = event.data
        sensor = data["sensor"]
        channel = str(data.get("channel") or "0")
        events: list[Event] = []

        if sensor == "weight":
            cell = self.cells.setdefault(channel, _Cell())
            previous = cell.latest
            cell.add(now, float(data["value"]))
            key = self._reverse_map.get(channel)
            if key is None or previous is None:
                return events
            shelf, slot = key.split("/", 1)
            view = self.slots.setdefault(key, _SlotView())
            delta = previous - float(data["value"])

            if delta >= self.pickup_g:
                interacted = now - cell.last_tof_s <= self.tof_window_s
                person_near = now - self.last_person_near_s.get(key, -1e9) <= self.person_near_window_s
                if interacted or person_near:
                    cell.last_pickup_s = now
                    events.append(make_event(
                        ts=clock.now(), store=self.store, node=self.node,
                        type=EventType.PICKUP,
                        data=PickupData(shelf=shelf, slot=slot, grams=round(delta, 1),
                                        evidence="tof+weight" if interacted else "weight+dwell")))
                else:
                    events.append(make_event(
                        ts=clock.now(), store=self.store, node=self.node,
                        type=EventType.SHRINK_FLAG,
                        data=ShrinkFlagData(shelf=shelf, slot=slot, grams=round(delta, 1),
                                            evidence="weight drop with nobody at the shelf")))

            # Hidden depletion: the camera is happy, the weight is not.
            if view.state is SlotState.FULL:
                lost = cell.drop_over(now, self.hidden_depletion_window_s)
                if lost >= self.hidden_depletion_g:
                    self.findings.append({"type": "HIDDEN_DEPLETION", "slot": key,
                                          "grams_lost": round(lost, 1), "t": now})

        elif sensor == "tof":
            cell = self.cells.setdefault(channel, _Cell())
            cell.last_tof_mm = float(data["value"])
            # A hand in the shelf is a *near* reading; an empty gap reads far.
            if cell.last_tof_mm < 250:
                cell.last_tof_s = now

        elif sensor == "beam":
            # IR break-beam at the door: independent ground truth for camera counts.
            self.findings.append({"type": "DOOR_BEAM", "t": now})

        return events

    def _on_zone_visit(self, event: Event, clock: Clock) -> list[Event]:
        now = clock.monotonic_s()
        zone = event.data["zone"]
        key = self.zone_to_slot.get(zone)
        if key is None:
            return []
        self.last_person_near_s[key] = now
        dwell = float(event.data["dwell_s"])
        view = self.slots.setdefault(key, _SlotView())
        if view.state not in (SlotState.EMPTY, SlotState.LOW):
            return []
        if dwell < self.dwell_lost_sale_s:
            return []
        shelf, slot = key.split("/", 1)
        view.shoppers_checked += 1
        # Risk is the shelf price of one unit: this shopper came for it and could
        # not buy it.  Stated as risk, never as booked revenue.
        estimate = float(view.price or 0.0)
        view.risk_inr += estimate
        self.total_risk_inr += estimate
        return [make_event(
            ts=clock.now(), store=self.store, node=self.node,
            type=EventType.LOST_SALE_RISK,
            data=LostSaleRiskData(shelf=shelf, slot=slot, sku=view.sku, price=view.price,
                                  dwell_s=round(dwell, 1), est_value=round(estimate, 2)))]

    # ------------------------------------------------------------------ #
    def tick(self, clock: Clock) -> list[Event]:
        out, self._pending = self._pending, []
        return out

    def summary(self) -> dict:
        return {
            "total_lost_sale_risk_inr": round(self.total_risk_inr, 2),
            "slots": {key: {"state": view.state.value, "sku": view.sku,
                            "shoppers_checked": view.shoppers_checked,
                            "risk_inr": round(view.risk_inr, 2)}
                      for key, view in self.slots.items()},
            "findings": self.findings[-50:],
            "cells": {channel: {"latest_g": cell.latest} for channel, cell in self.cells.items()},
        }
