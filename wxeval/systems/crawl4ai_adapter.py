"""crawl4ai in its two extraction modes.

crawl4ai_llm     LLMExtractionStrategy: the LLM reads each page as markdown (the default
                 input format), produced by crawl4ai's own markdown generator
crawl4ai_schema  generate_schema writes CSS selectors once on the development site, then
                 JsonCssExtractionStrategy runs with no LLM calls

Pages come from the shared fetcher, so every system sees the same rendered HTML. The
strategies are called directly instead of through AsyncWebCrawler, which would start a
second browser for each page.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from ..llm import MODEL, DiskCache, price
from .base import Context, System, Task, crawl

PROVIDER = f"anthropic/{MODEL}"
# crawl4ai sends temperature=0.01 by default, which Haiku 5.5 rejects with a 400.
# None makes litellm leave the field out.
EXTRA_ARGS: dict[str, Any] = {"temperature": None, "max_tokens": 16000}


def _llm_config() -> Any:
    from crawl4ai import LLMConfig

    return LLMConfig(provider=PROVIDER, api_token=os.environ.get("ANTHROPIC_API_KEY"))


class Crawl4AIPerPage(System):
    name = "crawl4ai_llm"
    uses_llm = True

    def extract(self, task: Task, ctx: Context) -> list[dict[str, Any]]:
        import crawl4ai
        from crawl4ai import LLMExtractionStrategy
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

        schema = {
            "type": "object",
            "properties": {
                "records": {"type": "array", "items": task.schema.json_schema()},
                "next_page_url": {"type": ["string", "null"]},
            },
        }
        instruction = (f"Extract every {task.schema.record} on the page with these fields:\n"
                       f"{task.schema.describe()}\nAlso give the URL of the next page of "
                       "results as next_page_url, or null.")
        cache = DiskCache(ctx.cache_dir)
        markdown = DefaultMarkdownGenerator()

        def page(html: str, url: str) -> tuple[list[dict[str, Any]], str | None]:
            md = markdown.generate_markdown(html, base_url=url).raw_markdown
            key = DiskCache.key({"tool": "crawl4ai_llm", "version": crawl4ai.__version__,
                                 "model": MODEL, "input": md, "instruction": instruction,
                                 "schema": schema})
            hit = cache.get(key)
            if hit is None:
                ctx.ledger.check(price(MODEL, len(md) // 3, 4000))
                strategy = LLMExtractionStrategy(
                    llm_config=_llm_config(), schema=schema, extraction_type="schema",
                    instruction=instruction, apply_chunking=False, extra_args=EXTRA_ARGS)
                start = time.perf_counter()
                blocks = strategy.run(url, [md])
                usage = strategy.total_usage
                hit = {"blocks": blocks, "in": usage.prompt_tokens,
                       "out": usage.completion_tokens, "seconds": time.perf_counter() - start}
                hit["cost"] = price(MODEL, hit["in"], hit["out"])
                ctx.ledger.record(self.name, MODEL, hit["in"], hit["out"], hit["cost"],
                                  hit["seconds"], cached=False)
                failed = [b for b in blocks if isinstance(b, dict) and b.get("error")]
                if failed or not blocks:
                    # Failed calls are paid for but never cached.
                    raise RuntimeError(f"crawl4ai returned {str(failed or blocks)[:200]}")
                cache.put(key, hit)
            else:
                ctx.ledger.record(self.name, MODEL, hit["in"], hit["out"], hit["cost"],
                                  hit["seconds"], cached=True)
            records: list[dict[str, Any]] = []
            nxt = None
            for block in hit["blocks"]:
                if not isinstance(block, dict):
                    continue
                if isinstance(block.get("records"), list):
                    records.extend(r for r in block["records"] if isinstance(r, dict))
                    nxt = nxt or block.get("next_page_url")
                else:
                    records.append(block)
            return records, nxt

        return crawl(task.start_url, ctx, page)


class Crawl4AISchema(System):
    name = "crawl4ai_schema"
    uses_llm = True

    def __init__(self) -> None:
        self.schemas: dict[str, dict[str, Any]] = {}

    def prepare(self, dev: Task, ctx: Context) -> None:
        import crawl4ai
        from crawl4ai import JsonCssExtractionStrategy
        from crawl4ai.models import TokenUsage

        html = ctx.fetcher.get(dev.start_url)
        query = (f"Extract every {dev.schema.record} with these fields:\n"
                 f"{dev.schema.describe()}")
        example = json.dumps({n: "..." for n in dev.schema.names()})
        cache = DiskCache(ctx.cache_dir)
        key = DiskCache.key({"tool": "crawl4ai_schema", "version": crawl4ai.__version__,
                             "model": MODEL, "html": html, "query": query})
        hit = cache.get(key)
        if hit is None:
            # Up to four calls with validation and refinement.
            ctx.ledger.check(4 * price(MODEL, len(html) // 3, 4000))
            usage = TokenUsage()
            start = time.perf_counter()
            try:
                # generate_schema passes extra keyword arguments on to the LLM call.
                schema = JsonCssExtractionStrategy.generate_schema(
                    html=html, query=query, target_json_example=example,
                    llm_config=_llm_config(), validate=True, usage=usage, **EXTRA_ARGS)
            finally:
                cost = price(MODEL, usage.prompt_tokens, usage.completion_tokens)
                ctx.ledger.record(self.name, MODEL, usage.prompt_tokens,
                                  usage.completion_tokens, cost, time.perf_counter() - start,
                                  cached=False)
            hit = {"schema": schema, "in": usage.prompt_tokens, "out": usage.completion_tokens,
                   "seconds": time.perf_counter() - start, "cost": cost}
            cache.put(key, hit)
        else:
            ctx.ledger.record(self.name, MODEL, hit["in"], hit["out"], hit["cost"],
                              hit["seconds"], cached=True)
        (ctx.workdir / f"{dev.kind}.json").write_text(json.dumps(hit["schema"], indent=1))
        self.schemas[dev.kind] = hit["schema"]

    def extract(self, task: Task, ctx: Context) -> list[dict[str, Any]]:
        from crawl4ai import JsonCssExtractionStrategy

        strategy = JsonCssExtractionStrategy(self.schemas[task.kind])

        def page(html: str, url: str) -> tuple[list[dict[str, Any]], str | None]:
            return strategy.extract(url, html), None

        return crawl(task.start_url, ctx, page, fallback_next=True)
