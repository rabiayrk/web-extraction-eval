"""Page fetching shared by all systems, with request counting.

Two modes:
  http     plain GET, no JavaScript
  browser  Chromium through Playwright; scrolls and clicks "Load more" until the page
           stops growing, then returns the final DOM
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

USER_AGENT = "web-extraction-eval/0.1 (+https://github.com/rabiayrk/web-extraction-eval)"


@dataclass
class FetchStats:
    pages: int = 0
    requests: int = 0
    seconds: float = 0.0


class Fetcher:
    def __init__(self, mode: str = "browser", max_rounds: int = 30) -> None:
        if mode not in ("http", "browser"):
            raise ValueError(mode)
        self.mode = mode
        self.max_rounds = max_rounds
        self.stats = FetchStats()
        self._client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30,
                                    follow_redirects=True)
        self._pw: Any = None
        self._browser: Any = None

    def reset_stats(self) -> FetchStats:
        old, self.stats = self.stats, FetchStats()
        return old

    def get(self, url: str) -> str:
        start = time.perf_counter()
        try:
            if self.mode == "http":
                self.stats.requests += 1
                resp = self._client.get(url)
                resp.raise_for_status()
                return resp.text
            return self._render(url)
        finally:
            self.stats.pages += 1
            self.stats.seconds += time.perf_counter() - start

    def _render(self, url: str) -> str:
        if self._browser is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch()
        page = self._browser.new_page(user_agent=USER_AGENT)
        page.on("request", lambda _req: self._count())
        try:
            page.goto(url, wait_until="networkidle")
            last = -1
            for _ in range(self.max_rounds):
                size = page.evaluate("document.body.innerHTML.length")
                button = page.locator("button", has_text="Load more")
                if button.count() and button.first.is_visible():
                    button.first.click()
                else:
                    page.mouse.wheel(0, 20000)
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(150)
                if size == last and not button.count():
                    break
                last = size
            return str(page.content())
        finally:
            page.close()

    def _count(self) -> None:
        self.stats.requests += 1

    def close(self) -> None:
        self._client.close()
        if self._browser is not None:
            self._browser.close()
            self._pw.stop()
            self._browser = None

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
