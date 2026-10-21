"""Conservative persisted cost reservations for the small public benchmark only.

One runner must hold the ledger lock. Unknown/failed requests retain reservations.
Rates are explicit USD list-price estimates, not a provider billing guarantee.
"""

import json
import math
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from app.services.state import save_state


class BudgetExceeded(RuntimeError):
    pass


class BudgetLedger:
    def __init__(self, path: Path, limit: float):
        if not math.isfinite(limit) or not 0 < limit <= 0.50:
            raise ValueError("Public benchmark budget must be between 0 and USD 0.50")
        self.path, self.limit = path, limit
        self.data = json.loads(path.read_text()) if path.exists() else {
            "known_cost_usd": 0.0, "accounted_usd": 0.0, "calls": 0, "uncertain_calls": 0}
        self.validate()

    def validate(self):
        for field in ("known_cost_usd", "accounted_usd"):
            value = self.data[field]
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("Corrupt budget ledger; paid calls are disabled")
        if self.data["known_cost_usd"] > self.data["accounted_usd"] + 1e-8:
            raise ValueError("Inconsistent budget ledger")
        for field in ("calls", "uncertain_calls"):
            if type(self.data[field]) is not int or self.data[field] < 0:
                raise ValueError("Invalid budget counters")

    @contextmanager
    def lock(self):
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Re-read after locking, so a resumed process cannot use stale balances.
            if self.path.exists():
                self.data = json.loads(self.path.read_text())
            self.validate()
            yield self

    def reserve(self, upper: float):
        if not math.isfinite(upper) or upper <= 0:
            raise ValueError("Invalid reservation")
        if self.data["accounted_usd"] + upper > self.limit:
            raise BudgetExceeded("Conservative API budget exhausted before sending a request")
        self.data["accounted_usd"] += upper
        self.data["uncertain_calls"] += 1
        self.data["calls"] += 1
        save_state(self.path, self.data)

    def settle(self, upper: float, actual: float):
        if not math.isfinite(actual) or actual < 0 or actual > upper:
            raise BudgetExceeded("Usage exceeds the reserved bound; stop and audit billing")
        self.data["accounted_usd"] -= upper - actual
        self.data["known_cost_usd"] += actual
        self.data["uncertain_calls"] -= 1
        save_state(self.path, self.data)


class BudgetClient:
    """Wrap the real SDK, restricting model/tier/output/tool count and request size."""

    def __init__(self, client, ledger: BudgetLedger):
        self.client, self.ledger = client, ledger
        self.responses = SimpleNamespace(create=self.respond)
        self.embeddings = SimpleNamespace(create=self.embed)

    def respond(self, **kwargs):
        if kwargs.get("model") != "gpt-6-luna":
            raise ValueError("Only the approved gpt-6-luna model has a cost bound")
        if len(json.dumps(kwargs).encode()) > 50_000:
            raise ValueError("Benchmark model payload exceeds the bounded public corpus size")
        if any(tool.get("type") != "file_search" for tool in kwargs.get("tools", [])):
            raise ValueError("No unbudgeted tools are allowed")
        kwargs.update(max_output_tokens=1500, store=False, service_tier="default",
                      reasoning={"effort": "none"})
        if kwargs.get("tools"):
            kwargs["max_tool_calls"] = 1
        # <=50k request bytes plus a conservatively allowed 64k tool-context tokens,
        # 1500 output tokens and one $0.0025 search call, at $0.10/$0.50 per million.
        # USD .02 leaves headroom for protocol overhead. Corpus is verified separately.
        upper = 0.02
        self.ledger.reserve(upper)
        response = self.client.responses.create(**kwargs)
        usage = response.usage
        if usage is None:
            raise BudgetExceeded("Missing provider usage; reservation retained")
        calls = sum(getattr(item, "type", None) == "file_search_call" for item in response.output)
        actual = (usage.input_tokens * 0.10 + usage.output_tokens * 0.50) / 1_000_000
        actual += calls * 0.0025
        self.ledger.settle(upper, actual)
        return response

    def embed(self, **kwargs):
        if kwargs.get("model") != "text-embedding-3-small":
            raise ValueError("Unbudgeted embedding model")
        if len(json.dumps(kwargs).encode()) > 30_000:
            raise ValueError("Embedding payload exceeds the public benchmark bound")
        upper = 0.001
        self.ledger.reserve(upper)
        response = self.client.embeddings.create(**kwargs)
        self.ledger.settle(upper, response.usage.total_tokens * 0.02 / 1_000_000)
        return response
