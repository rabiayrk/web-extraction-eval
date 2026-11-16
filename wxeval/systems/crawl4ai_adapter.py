"""crawl4ai in its two extraction modes.

crawl4ai_llm     LLMExtractionStrategy: the LLM reads each page (as markdown, the default)
crawl4ai_schema  generate_schema writes CSS selectors once on the development site, then
                 JsonCssExtractionStrategy runs with no LLM calls

Pages come from the shared fetcher, so every system sees the same rendered HTML.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any

from parsel import Selector

from ..llm import MODEL, DiskCache, price
from .base import Context, System, Task, crawl

PROVIDER = f"anthropic/{MODEL}"
# Haiku 5.5 rejects non default sampling values; crawl4ai sends temperature 0.01 unless
# it is overridden. None makes litellm leave the field out.
EXTRA_ARGS = {"temperature": None, "max_tokens": 16000}


def _llm_config() -> Any:
    from crawl4ai import LLMConfig

    return LLMConfig(provider=PROVIDER, api_token=os.environ.get("ANTHROPIC_API_KEY"))


def _cache(ctx: Context) -> DiskCache:
    return DiskCache(ctx.cache_dir)


def next_link(html: str) -> str | None:
    """Generic next link rule used with schema mode, which only extracts records."""
    sel = Selector(html)
    for a in sel.css("a[href]"):
        text = " ".join(a.css("::text").getall()).strip().lower()
        if a.attrib.get("rel") == "next" or re.match(r"^next\b", text):
            return a.attrib["href"]
    return None


class Crawl4AIPerPage(System):
    name = "crawl4ai_llm"
    uses_llm = True

    def extract(self, task: Task, ctx: Context) -> list[dict[str, Any]]:
        import crawl4ai
        from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig, LLMExtractionStrategy

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
        cache = _cache(ctx)

        async def run_page(html: str, url: str) -> dict[str, Any]:
            strategy = LLMExtractionStrategy(
                llm_config=_llm_config(), schema=schema, extraction_type="schema",
                instruction=instruction, apply_chunking=False, extra_args=EXTRA_ARGS)
            config = CrawlerRunConfig(extraction_strategy=strategy, cache_mode=CacheMode.BYPASS)
            start = time.perf_counter()
            async with AsyncWebCrawler() as crawler:
                result = await crawler.arun(url="raw:" + html, config=config)
            usage = strategy.total_usage
            blocks = json.loads(result.extracted_content or "[]")
            return {"blocks": blocks, "in": usage.prompt_tokens,
                    "out": usage.completion_tokens, "seconds": time.perf_counter() - start}

        def page(html: str, url: str) -> tuple[list[dict[str, Any]], str | None]:
            key = DiskCache.key({"tool": "crawl4ai_llm", "version": crawl4ai.__version__,
                                 "model": MODEL, "html": html, "instruction": instruction,
                                 "schema": schema})
            hit = cache.get(key)
            if hit is None:
                ctx.ledger.check(price(MODEL, len(html) // 3, 4000))
                hit = asyncio.run(run_page(html, url))
                hit["cost"] = price(MODEL, hit["in"], hit["out"])
                cache.put(key, hit)
                ctx.ledger.record(self.name, MODEL, hit["in"], hit["out"], hit["cost"],
                                  hit["seconds"], cached=False)
            else:
                ctx.ledger.record(self.name, MODEL, hit["in"], hit["out"], hit["cost"],
                                  hit["seconds"], cached=True)
            records: list[dict[str, Any]] = []
            nxt = None
            for block in hit["blocks"]:
                if not isinstance(block, dict) or block.get("error"):
                    continue
                if isinstance(block.get("records"), list):
                    records.extend(r for r in block["records"] if isinstance(r, dict))
                    nxt = nxt or block.get("next_page_url")
                else:
                    records.append(block)
            return records, nxt

        return crawl(task.start_url, ctx.fetcher, page)


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
        cache = _cache(ctx)
        key = DiskCache.key({"tool": "crawl4ai_schema", "version": crawl4ai.__version__,
                             "model": MODEL, "html": html, "query": query})
        hit = cache.get(key)
        if hit is None:
            ctx.ledger.check(price(MODEL, len(html) // 3 * 4, 16000))
            usage = TokenUsage()
            start = time.perf_counter()
            schema = JsonCssExtractionStrategy.generate_schema(
                html=html, query=query, target_json_example=example, llm_config=_llm_config(),
                validate=True, usage=usage, extra_args=EXTRA_ARGS)
            hit = {"schema": schema, "in": usage.prompt_tokens, "out": usage.completion_tokens,
                   "seconds": time.perf_counter() - start}
            hit["cost"] = price(MODEL, hit["in"], hit["out"])
            cache.put(key, hit)
            ctx.ledger.record(self.name, MODEL, hit["in"], hit["out"], hit["cost"],
                              hit["seconds"], cached=False)
        else:
            ctx.ledger.record(self.name, MODEL, hit["in"], hit["out"], hit["cost"],
                              hit["seconds"], cached=True)
        (ctx.workdir / f"{dev.kind}.json").write_text(json.dumps(hit["schema"], indent=1))
        self.schemas[dev.kind] = hit["schema"]

    def extract(self, task: Task, ctx: Context) -> list[dict[str, Any]]:
        from crawl4ai import JsonCssExtractionStrategy

        strategy = JsonCssExtractionStrategy(self.schemas[task.kind])

        def page(html: str, url: str) -> tuple[list[dict[str, Any]], str | None]:
            return strategy.extract(url, html), next_link(html)

        return crawl(task.start_url, ctx.fetcher, page)
