"""Small check on a public practice site: quotes.toscrape.com, run by Zyte for scraping
practice. robots.txt is checked first, there is at least two seconds between requests and
every page is fetched once and cached, so all systems share the same copy.

Reference records come from the plain HTML version (/page/N/). Systems are scored on the
JavaScript version (/js/) and the infinite scroll version (/scroll). Quote texts are not
stored in this repository; only scores are.

  uv run python scripts/live_quotes.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.robotparser
from pathlib import Path

from parsel import Selector

from wxeval.eval.metrics import evaluate
from wxeval.fetch import USER_AGENT, Fetcher
from wxeval.llm import Claude, DiskCache, Ledger
from wxeval.schemas import QUOTES
from wxeval.systems import registry
from wxeval.systems.base import Context, Task

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://quotes.toscrape.com"
DELAY = 2.0


class PoliteFetcher(Fetcher):
    def __init__(self, mode: str, cache: Path) -> None:
        # Scrolling triggers more requests, so pause between scroll steps too.
        super().__init__(mode, pause_ms=int(DELAY * 1000))
        self.cache = cache
        self.last = 0.0
        self.robots = urllib.robotparser.RobotFileParser(SITE + "/robots.txt")
        self.robots.read()

    def get(self, url: str) -> str:
        if not url.startswith(SITE):
            raise ValueError(f"refusing to fetch outside {SITE}: {url}")
        if not self.robots.can_fetch(USER_AGENT, url):
            raise PermissionError(f"robots.txt disallows {url}")
        path = self.cache / self.mode / (hashlib.sha256(url.encode()).hexdigest() + ".html")
        if path.exists():
            self.stats.pages += 1
            return path.read_text()
        wait = DELAY - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        html = super().get(url)
        self.last = time.monotonic()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html)
        return html


def reference(fetcher: Fetcher) -> list[dict[str, str]]:
    records = []
    url: str | None = SITE + "/page/1/"
    while url:
        sel = Selector(fetcher.get(url))
        for q in sel.css("div.quote"):
            records.append({
                "text": q.css("span.text::text").get("").strip("“”\" "),
                "author": q.css("small.author::text").get(""),
                "tags": ", ".join(q.css("a.tag::text").getall()),
            })
        nxt = sel.css("li.next a::attr(href)").get()
        url = SITE + nxt if nxt else None
    return records


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--systems", default="llm_page,llm_code,crawl4ai_llm,crawl4ai_schema,"
                                         "scrapegraph")
    ap.add_argument("--budget", type=float,
                    default=float(os.environ.get("WXEVAL_BUDGET_USD", "20")))
    args = ap.parse_args()
    cache = ROOT / ".cache"
    results = ROOT / "results"
    ledger = Ledger(results / "spend.jsonl", args.budget)
    claude = Claude(DiskCache(cache / "llm"), ledger)
    with PoliteFetcher("http", cache / "live") as plain:
        gold = reference(plain)
    rows = []
    with PoliteFetcher("browser", cache / "live") as browser:
        for name in args.systems.split(","):
            system = registry.load(name)
            ctx = Context(browser, ledger, claude, ROOT / "build" / "work-live" / name,
                          cache / "tools")
            ctx.workdir.mkdir(parents=True, exist_ok=True)
            ledger.take_usage()
            system.prepare(Task("quotes", SITE + "/page/1/", QUOTES), ctx)
            for label, path in (("js", "/js/"), ("scroll", "/scroll")):
                browser.reset_stats()
                try:
                    records, error = system.extract(Task("quotes", SITE + path, QUOTES), ctx), ""
                except Exception as exc:
                    if type(exc).__name__ == "BudgetExceeded":
                        raise
                    records, error = [], f"{type(exc).__name__}: {exc}"[:200]
                res = evaluate(records, gold, QUOTES, "text")
                usage = ledger.take_usage()
                rows.append({"system": name, "page": label, "field_f1": res.fields.f1,
                             "coverage": res.coverage, "duplicate_rate": res.duplicate_rate,
                             "records": len(records), "cost_usd": usage.cost, "error": error})
                print(rows[-1])
    lines = ["| System | Version | Field F1 | Coverage | Duplicate rate | Records | Cost USD |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['system']} | {r['page']} | {r['field_f1']:.2f} | "
                     f"{r['coverage']:.2f} | {r['duplicate_rate']:.3f} | {r['records']} | "
                     f"{r['cost_usd']:.4f} |" + (" error" if r["error"] else ""))
    header = (f"# quotes.toscrape.com check\n\nReference: {len(gold)} quotes from the plain "
              f"HTML pages. Fetched {time.strftime('%Y-%m-%d')}.\n\n")
    (results / "live_quotes.md").write_text(header + "\n".join(lines) + "\n")
    (results / "live_quotes.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
