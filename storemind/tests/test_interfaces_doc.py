"""Contract test: every event example in docs/INTERFACES.md is a valid event on its topic."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from storemind.core.bus import topic_for
from storemind.core.config import SensorConfig
from storemind.core.events import Event, EventType

DOC = Path(__file__).resolve().parents[2] / "docs" / "INTERFACES.md"
TEXT = DOC.read_text(encoding="utf-8")
EXAMPLES = re.findall(r"Topic: `([^`]+)`[^\n]*\n\n```json\n(.*?)\n```", TEXT, flags=re.DOTALL)


def test_every_v2_type_has_an_example():
    types = {json.loads(body)["type"] for _, body in EXAMPLES}
    v2 = {"SHELF_MOTION", "CAMERA_MOUNT", "BEAM_CROSS", "PRESENCE", "ENVIRONMENT", "WEIGHT",
          "NODE_HEALTH", "CAMERA_HEALTH"}
    assert v2 <= types


@pytest.mark.parametrize("topic, body", EXAMPLES)
def test_example_validates_and_matches_its_topic(topic, body):
    raw = json.loads(body)
    event = Event(**raw)
    assert topic_for(event) == topic
    # The example is already in normalised form: validating it changes nothing.
    assert event.data == raw["data"]


def test_every_event_type_is_documented():
    for event_type in EventType:
        assert f"`{event_type.value}`" in TEXT, event_type


def test_sensors_yaml_example_validates():
    import yaml

    block = re.search(r"```yaml\n(sensors:.*?)```", TEXT, flags=re.DOTALL).group(1)
    SensorConfig(**yaml.safe_load(block)["sensors"])
