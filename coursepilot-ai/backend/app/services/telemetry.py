import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def append_event(path: Path, event: Dict[str, Any]) -> None:
    """Append privacy-conscious operational telemetry as one JSONL record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def estimate_cost(
    usage: Dict[str, Any], input_cost_per_million: float, output_cost_per_million: float
) -> float:
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cost = (
        input_tokens * input_cost_per_million
        + output_tokens * output_cost_per_million
    ) / 1_000_000
    return round(cost, 8)
