from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urljoin

from ..fetch import Fetcher
from ..llm import Claude, Ledger
from ..schemas import Schema

MAX_PAGES = 20


@dataclass
class Task:
    kind: str
    start_url: str
    schema: Schema


@dataclass
class Context:
    fetcher: Fetcher
    ledger: Ledger
    claude: Claude | None
    workdir: Path  # per system folder for artifacts such as generated code
    cache_dir: Path  # for adapters that cache tool output themselves


class System(ABC):
    """A system under test. Adding one means writing a subclass in its own file."""

    name: ClassVar[str]
    uses_llm: ClassVar[bool] = False

    def prepare(self, dev: Task, ctx: Context) -> None:
        """One time setup on the development site. Never sees test sites."""
        return None

    @abstractmethod
    def extract(self, task: Task, ctx: Context) -> list[dict[str, Any]]:
        """Return every record found, starting from task.start_url."""


PageFn = Callable[[str, str], tuple[list[dict[str, Any]], str | None]]


def crawl(start_url: str, fetcher: Fetcher, page_fn: PageFn,
          max_pages: int = MAX_PAGES) -> list[dict[str, Any]]:
    """Follow next links. page_fn gets (html, url) and returns (records, next_url)."""
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    url: str | None = start_url
    while url and url not in seen and len(seen) < max_pages:
        seen.add(url)
        html = fetcher.get(url)
        found, nxt = page_fn(html, url)
        records.extend(found)
        url = urljoin(url, nxt) if nxt else None
    return dedupe(records)


def dedupe(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop exact repeats only. Near copies stay, so wrong merges are not hidden."""
    out, seen = [], set()
    for r in records:
        key = repr(sorted(r.items()))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out
