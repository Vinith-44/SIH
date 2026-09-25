"""docs/CONFIG_REFERENCE.md must list every config key that core/config.py defines."""

from __future__ import annotations

import typing
from pathlib import Path

from pydantic import BaseModel

from storemind.core.config import StoreMindConfig

DOC = Path(__file__).resolve().parents[2] / "docs" / "CONFIG_REFERENCE.md"


def _nested(annotation) -> tuple[type[BaseModel] | None, bool]:
    for outer in (annotation, *typing.get_args(annotation)):
        for inner in (outer, *typing.get_args(outer)):
            if isinstance(inner, type) and issubclass(inner, BaseModel):
                return inner, typing.get_origin(outer) is list
    return None, False


def config_keys(model: type[BaseModel] = StoreMindConfig, prefix: str = "") -> list[str]:
    keys = []
    for name, field in model.model_fields.items():
        path = prefix + name
        keys.append(path)
        sub, is_list = _nested(field.annotation)
        if sub is not None:
            keys += config_keys(sub, f"{path}[]." if is_list else f"{path}.")
    return keys


def test_every_config_key_is_documented():
    text = DOC.read_text(encoding="utf-8")
    missing = [key for key in config_keys() if f"`{key}`" not in text]
    assert not missing, f"add these keys to docs/CONFIG_REFERENCE.md: {missing}"


def test_walk_finds_nested_keys():
    keys = config_keys()
    assert "cameras[].shelves[].slots[].unit_grams" in keys and "sensors.node.id" in keys
