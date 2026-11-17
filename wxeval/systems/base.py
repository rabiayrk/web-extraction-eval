from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urljoin, urlsplit, urlunsplit

from parsel import Selector

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
    errors: list[str] = field(default_factory=list)  # page level errors of the current run


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


def next_link(html: str) -> str | None:
    """Generic rule: a link with rel=next or text starting with "next"."""
    sel = Selector(html)
    for a in sel.css("a[href]"):
        text = " ".join(a.css("::text").getall()).strip().lower()
        if a.attrib.get("rel") == "next" or re.match(r"^next\b", text):
            return a.attrib["href"]
    return None


def local_url(current: str, nxt: str) -> str:
    """Resolve a next link. Absolute links to another local port (for example from a cached
    answer of an earlier run) are moved to the current server."""
    url = urljoin(current, nxt)
    cur, new = urlsplit(current), urlsplit(url)
    if new.hostname in ("127.0.0.1", "localhost") and new.netloc != cur.netloc:
        url = urlunsplit((cur.scheme, cur.netloc, new.path, new.query, new.fragment))
    return url


def crawl(start_url: str, ctx: Context, page_fn: PageFn,
          max_pages: int = MAX_PAGES) -> list[dict[str, Any]]:
    """Follow next links. page_fn gets (html, url) and returns (records, next_url).

    When a system gives no next link, the same generic rule is tried for every system, so
    that coverage measures extraction and not each tool's own paging support. A page that
    fails stops the crawl but keeps the records found so far.
    """
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    url: str | None = start_url
    while url and url not in seen and len(seen) < max_pages:
        seen.add(url)
        try:
            html = ctx.fetcher.get(url)
            found, nxt = page_fn(html, url)
        except Exception as exc:
            if type(exc).__name__ == "BudgetExceeded":
                raise
            ctx.errors.append(f"{url}: {type(exc).__name__}: {str(exc)[:200]}")
            break
        records.extend(found)
        if not nxt:
            nxt = next_link(html)
        url = local_url(url, nxt) if nxt else None
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
