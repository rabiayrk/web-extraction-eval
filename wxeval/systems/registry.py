from __future__ import annotations

import importlib

from .base import System

SYSTEMS = {
    "handwritten": "wxeval.systems.handwritten:Handwritten",
    "llm_page": "wxeval.systems.llm_page:LLMPerPage",
    "llm_code": "wxeval.systems.llm_code:LLMWrittenCode",
    "crawl4ai_llm": "wxeval.systems.crawl4ai_adapter:Crawl4AIPerPage",
    "crawl4ai_schema": "wxeval.systems.crawl4ai_adapter:Crawl4AISchema",
    "scrapegraph": "wxeval.systems.scrapegraph_adapter:ScrapeGraphSmart",
}

DEFAULT = list(SYSTEMS)


def load(name: str) -> System:
    module, cls = SYSTEMS[name].split(":")
    system: System = getattr(importlib.import_module(module), cls)()
    return system
