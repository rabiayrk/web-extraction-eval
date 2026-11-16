"""Claude client with a disk cache, a hard budget and a spend log.

Cost numbers in results are nominal: a cached call reports the tokens and cost of the
original call, so results do not depend on the cache state. The spend log only records
money actually spent.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MODEL = "claude-haiku-5-5"

# USD per 1M tokens, from the Anthropic pricing page on 2026-11-16.
# Prompts above 100K tokens use the second rate card.
@dataclass(frozen=True)
class PriceCard:
    limit: int
    low: tuple[float, float]
    high: tuple[float, float]


PRICES = {"claude-haiku-5-5": PriceCard(100_000, (0.10, 0.50), (0.50, 2.50))}


class BudgetExceeded(RuntimeError):
    pass


def price(model: str, input_tokens: int, output_tokens: int) -> float:
    card = PRICES[model]
    rate_in, rate_out = card.low if input_tokens <= card.limit else card.high
    return (input_tokens * rate_in + output_tokens * rate_out) / 1_000_000


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    seconds: float = 0.0
    cached_calls: int = 0

    def add(self, in_tok: int, out_tok: int, cost: float, seconds: float, cached: bool) -> None:
        self.calls += 1
        self.input_tokens += in_tok
        self.output_tokens += out_tok
        self.cost += cost
        self.seconds += seconds
        self.cached_calls += int(cached)


@dataclass
class Reply:
    text: str
    input_tokens: int
    output_tokens: int
    cost: float
    seconds: float
    cached: bool


class Ledger:
    def __init__(self, path: Path, budget_usd: float) -> None:
        self.path = path
        self.budget = budget_usd
        self.spent = 0.0
        if path.exists():
            for line in path.read_text().splitlines():
                self.spent += json.loads(line)["cost"]
        self.usage = Usage()

    def check(self, estimate: float) -> None:
        if self.spent + estimate > self.budget:
            raise BudgetExceeded(
                f"spent ${self.spent:.4f}, next call ~${estimate:.4f}, budget ${self.budget:.2f}")

    def record(self, source: str, model: str, in_tok: int, out_tok: int, cost: float,
               seconds: float, cached: bool) -> None:
        self.usage.add(in_tok, out_tok, cost, seconds, cached)
        if cached:
            return
        self.spent += cost
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            fh.write(json.dumps({
                "time": datetime.now(UTC).isoformat(timespec="seconds"), "source": source,
                "model": model, "input_tokens": in_tok, "output_tokens": out_tok,
                "cost": round(cost, 6),
            }) + "\n")

    def take_usage(self) -> Usage:
        old, self.usage = self.usage, Usage()
        return old


class DiskCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def key(payload: Any) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        path = self.root / key[:2] / f"{key}.json"
        if path.exists():
            data: dict[str, Any] = json.loads(path.read_text())
            return data
        return None

    def put(self, key: str, value: dict[str, Any]) -> None:
        path = self.root / key[:2] / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))


class Claude:
    def __init__(self, cache: DiskCache, ledger: Ledger, model: str = MODEL,
                 effort: str | None = None) -> None:
        self.cache = cache
        self.ledger = ledger
        self.model = model
        self.effort = effort
        self._client: Any = None

    def complete(self, source: str, system: str, user: str,
                 schema: dict[str, Any] | None = None, max_tokens: int = 16000) -> Reply:
        payload = {"model": self.model, "effort": self.effort, "system": system, "user": user,
                   "schema": schema, "max_tokens": max_tokens}
        key = DiskCache.key(payload)
        hit = self.cache.get(key)
        if hit is not None:
            self.ledger.record(source, self.model, hit["input_tokens"], hit["output_tokens"],
                               hit["cost"], hit["seconds"], cached=True)
            return Reply(hit["text"], hit["input_tokens"], hit["output_tokens"], hit["cost"],
                         hit["seconds"], True)

        # Rough upper estimate: about 3 characters per token, and a long answer.
        self.ledger.check(price(self.model, (len(system) + len(user)) // 3, 4000))
        if self._client is None:
            import anthropic

            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError("ANTHROPIC_API_KEY is not set and the answer is not cached")
            self._client = anthropic.Anthropic(max_retries=4)
        kwargs: dict[str, Any] = {
            "model": self.model, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        # Effort is left at the model default unless set, as the tool adapters do.
        config: dict[str, Any] = {}
        if self.effort is not None:
            config["effort"] = self.effort
        if schema is not None:
            config["format"] = {"type": "json_schema", "schema": schema}
        if config:
            kwargs["output_config"] = config
        start = time.perf_counter()
        with self._client.messages.stream(**kwargs) as stream:
            msg = stream.get_final_message()
        seconds = time.perf_counter() - start
        in_tok = msg.usage.input_tokens + (msg.usage.cache_read_input_tokens or 0) + (
            msg.usage.cache_creation_input_tokens or 0)
        out_tok = msg.usage.output_tokens
        cost = price(self.model, in_tok, out_tok)
        # Log the spend before anything else can fail.
        self.ledger.record(source, self.model, in_tok, out_tok, cost, seconds, cached=False)
        if msg.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"model stopped with {msg.stop_reason}")
        text = "".join(b.text for b in msg.content if b.type == "text")
        self.cache.put(key, {"text": text, "input_tokens": in_tok, "output_tokens": out_tok,
                             "cost": cost, "seconds": seconds, "model": self.model,
                             "message_id": msg.id})
        return Reply(text, in_tok, out_tok, cost, seconds, False)
