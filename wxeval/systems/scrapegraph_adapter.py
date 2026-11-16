"""ScrapeGraphAI SmartScraperGraph: the LLM reads each page.

The graph gets the rendered HTML from the shared fetcher as a local source, and a
Pydantic schema built from the task schema.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, create_model

from ..llm import MODEL, DiskCache, price
from ..schemas import Schema
from .base import Context, System, Task, crawl

PY_TYPES: dict[str, Any] = {"string": str, "number": float, "integer": int,
                            "boolean": bool, "date": str}


def page_model(schema: Schema) -> type[BaseModel]:
    fields: dict[str, Any] = {}
    for f in schema.fields:
        t = PY_TYPES[f.type]
        fields[f.name] = (t | None, None) if f.nullable else (t, ...)
    record = create_model("Record", **fields)
    return create_model("Page", records=(list[record], ...),  # type: ignore[valid-type]
                        next_page_url=(str | None, None))


class _Usage:
    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0


def _callback(usage: _Usage) -> Any:
    from langchain_core.callbacks import BaseCallbackHandler

    class Handler(BaseCallbackHandler):
        def on_llm_end(self, response: Any, **kwargs: Any) -> None:
            for gens in response.generations:
                for g in gens:
                    meta = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                    usage.input_tokens += meta.get("input_tokens", 0)
                    usage.output_tokens += meta.get("output_tokens", 0)

    return Handler()


class ScrapeGraphSmart(System):
    name = "scrapegraph"
    uses_llm = True

    def extract(self, task: Task, ctx: Context) -> list[dict[str, Any]]:
        import scrapegraphai
        from langchain_anthropic import ChatAnthropic
        from scrapegraphai.graphs import SmartScraperGraph

        model = page_model(task.schema)
        prompt = (f"Extract every {task.schema.record} on the page with these fields:\n"
                  f"{task.schema.describe()}\nAlso give the URL of the next page of results "
                  "as next_page_url, or null.")
        cache = DiskCache(ctx.cache_dir)
        version = getattr(scrapegraphai, "__version__", "2.3.1")

        def page(html: str, url: str) -> tuple[list[dict[str, Any]], str | None]:
            key = DiskCache.key({"tool": "scrapegraph", "version": version, "model": MODEL,
                                 "html": html, "prompt": prompt})
            hit = cache.get(key)
            if hit is None:
                ctx.ledger.check(price(MODEL, len(html) // 3, 4000))
                usage = _Usage()
                # The key comes from ANTHROPIC_API_KEY.
                llm = ChatAnthropic(  # type: ignore[call-arg]
                    model_name=MODEL, max_tokens_to_sample=16000, max_retries=4,
                    callbacks=[_callback(usage)])
                config = {"llm": {"model_instance": llm, "model_tokens": 200_000},
                          "verbose": False, "headless": True}
                start = time.perf_counter()
                graph = SmartScraperGraph(prompt=prompt, source=html, config=config,
                                          schema=model)
                try:
                    answer = graph.run()
                except Exception:
                    ctx.ledger.record(self.name, MODEL, usage.input_tokens, usage.output_tokens,
                                      price(MODEL, usage.input_tokens, usage.output_tokens),
                                      time.perf_counter() - start, cached=False)
                    raise
                hit = {"answer": answer, "in": usage.input_tokens, "out": usage.output_tokens,
                       "seconds": time.perf_counter() - start}
                hit["cost"] = price(MODEL, hit["in"], hit["out"])
                ctx.ledger.record(self.name, MODEL, hit["in"], hit["out"], hit["cost"],
                                  hit["seconds"], cached=False)
                if not isinstance(answer, dict) or "error" in answer:
                    # Failed answers are paid for but never cached.
                    raise RuntimeError(f"ScrapeGraphAI returned {str(answer)[:200]}")
                cache.put(key, hit)
            else:
                ctx.ledger.record(self.name, MODEL, hit["in"], hit["out"], hit["cost"],
                                  hit["seconds"], cached=True)
            answer = hit["answer"]
            if not isinstance(answer, dict):
                return [], None
            records = [r for r in answer.get("records") or [] if isinstance(r, dict)]
            return records, answer.get("next_page_url")

        return crawl(task.start_url, ctx, page)
