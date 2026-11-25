"""Pydantic model → OpenAI strict-mode JSON Schema.

Strict mode requires every property to be listed in ``required``, objects to set
``additionalProperties: false``, and only a subset of keywords. Optional fields
are expressed as nullable (``anyOf [..., null]``) and the model sends ``null``
for "not provided". Formats (uuid, date) are moved into the description; the
backend enforces them via Pydantic anyway.
"""

from typing import Any

from pydantic import BaseModel

_DROP = {"title", "default", "examples"}
# Enforced by Pydantic on the backend instead.
_UNSUPPORTED = {
    "format",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "pattern",
}


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    if "$defs" in schema:
        raise ValueError(f"{model.__name__}: nested models/enums unsupported; use Literal")
    return _clean(schema)


def _clean(node: Any) -> Any:
    if isinstance(node, list):
        return [_clean(n) for n in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    fmt = node.get("format")
    for key, value in node.items():
        if key in _DROP or key in _UNSUPPORTED:
            continue
        if key == "properties":
            # Keys here are field *names* (a field may be called "title"): never drop them.
            out[key] = {name: _clean(sub) for name, sub in value.items()}
        else:
            out[key] = _clean(value)
    if fmt and "description" not in out:
        out["description"] = {"uuid": "UUID", "date": "YYYY-MM-DD"}.get(fmt, fmt)
    if out.get("type") == "object":
        out["additionalProperties"] = False
        out["required"] = list(out.get("properties", {}))
    return out
