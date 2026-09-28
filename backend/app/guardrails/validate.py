import json

from app.guardrails.catalog import CATALOG, CUSTOM_FILTERS
from app.guardrails.safety import get_safety


class GuardrailError(ValueError):
    pass


def validate_selections(selections: list) -> list[dict]:
    if not isinstance(selections, list):
        raise GuardrailError("selections must be a list")
    if len(selections) > len(CATALOG):
        raise GuardrailError("too many options")
    seen: set[str] = set()
    cleaned: list[dict] = []
    for raw in selections:
        if not isinstance(raw, dict):
            raise GuardrailError("each selection must be an object")
        option_id = raw.get("option_id")
        if option_id not in CATALOG:
            raise GuardrailError("unknown option")
        if option_id in seen:
            raise GuardrailError("duplicate option")
        seen.add(option_id)
        params = raw.get("params") or {}
        if not isinstance(params, dict):
            raise GuardrailError("params must be an object")
        spec = CATALOG[option_id]["params"]
        unknown = set(params) - set(spec)
        if unknown:
            raise GuardrailError("unknown parameter")
        clean_params: dict = {}
        for name, rule in spec.items():
            if name not in params:
                clean_params[name] = rule["default"]
                continue
            value = params[name]
            if rule["type"] == "enum":
                if value not in rule["values"]:
                    raise GuardrailError(f"invalid parameter {name}")
            elif rule["type"] == "int":
                if isinstance(value, bool) or not isinstance(value, int):
                    raise GuardrailError(f"invalid parameter {name}")
                if not rule["min"] <= value <= rule["max"]:
                    raise GuardrailError(f"invalid parameter {name}")
            else:
                raise GuardrailError(f"invalid parameter {name}")
            clean_params[name] = value
        cleaned.append({"option_id": option_id, "params": clean_params})
    serialized = json.dumps(cleaned, sort_keys=True)
    result = get_safety().analyze_content(serialized)
    if result.blocked:
        raise GuardrailError("rejected by content safety")
    return cleaned


def parse_custom_filter(raw: str | None) -> tuple[str, str] | None:
    if raw is None or raw == "":
        return None
    if ":" not in raw:
        raise GuardrailError("custom filter must be name:value")
    name, value = raw.split(":", 1)
    spec = CUSTOM_FILTERS.get(name)
    if spec is None:
        raise GuardrailError("unknown custom filter")
    if spec["op"] == "eq":
        if value not in spec["values"]:
            raise GuardrailError("invalid custom filter value")
    else:
        try:
            number = float(value)
        except ValueError as exc:
            raise GuardrailError("invalid custom filter value") from exc
        if not spec["min"] <= number <= spec["max"]:
            raise GuardrailError("invalid custom filter value")
    safety = get_safety().shield_prompt(raw)
    if safety.blocked:
        raise GuardrailError("rejected by content safety")
    return name, value


def matches_custom(layer2: dict | None, name: str, value: str) -> bool:
    spec = CUSTOM_FILTERS[name]
    node: object = layer2 or {}
    for part in spec["path"]:
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    if spec["op"] == "eq":
        return str(node) == value
    try:
        return float(node) >= float(value)
    except (TypeError, ValueError):
        return False
