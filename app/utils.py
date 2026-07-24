import json
from typing import Any


def to_json_list(values: list[Any]) -> str:
    return json.dumps(values)


def from_json_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def dict_get(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Read a value accepting either snake_case or camelCase keys."""
    for key in keys:
        if key in data:
            return data[key]
    return default
