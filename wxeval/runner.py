"""Build the sites, run each system on each site instance and store raw outputs."""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
import traceback
from dataclasses import asdict
from importlib import metadata
from pathlib import Path
from typing import Any

from .fetch import Fetcher
from .llm import MODEL, Claude, DiskCache, Ledger
from .schemas import SCHEMAS
from .sites.render import SiteInstance, build_site
from .sites.server import SiteServer
from .sites.variants import VARIANTS
from .systems.base import Context, System, Task

DEV_SEED = 0
# A fixed port keeps page URLs, and so prompts and cache keys, the same across runs.
PORT = 8765


def versions() -> dict[str, str]:
    out = {}
    for pkg in ("crawl4ai", "scrapegraphai", "langchain-anthropic", "anthropic", "playwright",
                "parsel"):
        with contextlib.suppress(metadata.PackageNotFoundError):
            out[pkg] = metadata.version(pkg)
    return out


def build_all(out: Path, kinds: list[str], variants: list[str],
              seeds: list[int]) -> list[SiteInstance]:
    sites = []
    for kind in kinds:
        build_site(kind, VARIANTS["base"], DEV_SEED, out)
        for name in variants:
            for seed in seeds:
                sites.append(build_site(kind, VARIANTS[name], seed, out))
    return sites


def run(systems: list[System], kinds: list[str], variants: list[str], seeds: list[int],
        build: Path, results: Path, cache: Path, budget: float, fetch_mode: str = "browser",
        log: Any = print) -> None:
    sites = build_all(build, kinds, variants, seeds)
    ledger = Ledger(results / "spend.jsonl", budget)
    claude = Claude(DiskCache(cache / "llm"), ledger)
    raw = results / "raw" / fetch_mode
    setup = {"fetch_mode": fetch_mode, "model": MODEL, "effort": "model default",
             "versions": versions()}
    with SiteServer(build / "www", PORT) as server, Fetcher(fetch_mode) as fetcher:
        for system in systems:
            ctx = Context(fetcher, ledger, claude if system.uses_llm else None,
                          build / "work" / system.name, cache / "tools")
            ctx.workdir.mkdir(parents=True, exist_ok=True)
            for kind in kinds:
                dev = Task(kind, f"{server.base_url}/{kind}/base/s{DEV_SEED}/list/page-1.html",
                           SCHEMAS[kind])
                fetcher.reset_stats()
                ledger.take_usage()
                prep_error = None
                try:
                    system.prepare(dev, ctx)
                except Exception as exc:
                    if type(exc).__name__ == "BudgetExceeded":
                        raise
                    prep_error = "prepare failed: " + "".join(
                        traceback.format_exception_only(exc)).strip()[:300]
                    log(f"{system.name:>18} {kind} {prep_error}")
                prep_fetch, prep_llm = fetcher.reset_stats(), ledger.take_usage()
                _write(raw / system.name / kind / "prepare.json", {
                    **setup, "fetch": asdict(prep_fetch), "llm": asdict(prep_llm),
                    "error": prep_error})
                for site in [s for s in sites if s.kind == kind]:
                    task = Task(kind, server.base_url + site.start_path, SCHEMAS[kind])
                    error = prep_error
                    records: list[dict[str, Any]] = []
                    ctx.errors.clear()
                    start = time.perf_counter()
                    if prep_error is None:
                        try:
                            records = system.extract(task, ctx)
                        except Exception as exc:
                            if type(exc).__name__ == "BudgetExceeded":
                                raise
                            error = "".join(traceback.format_exception_only(exc)).strip()
                    seconds = time.perf_counter() - start
                    fetch, usage = fetcher.reset_stats(), ledger.take_usage()
                    out = raw / system.name / kind / site.variant.name / f"s{site.seed}.json"
                    _write(out, {
                        **setup, "system": system.name, "kind": kind,
                        "variant": site.variant.name, "seed": site.seed,
                        "truth_sha256": hashlib.sha256(site.truth.read_bytes()).hexdigest(),
                        "records": records, "error": error, "page_errors": list(ctx.errors),
                        "wall_seconds": round(seconds, 3),
                        # Nominal time: browser time plus original LLM latency, cache or not.
                        "nominal_seconds": round(fetch.seconds + usage.seconds, 3),
                        "fetch": asdict(fetch), "llm": asdict(usage),
                    })
                    note = error or (ctx.errors[0] if ctx.errors else "")
                    log(f"{system.name:>18} {kind} {site.variant.name:<15} s{site.seed} "
                        f"records={len(records):>3} cost=${usage.cost:.4f}"
                        + (f" ERROR {note[:100]}" if note else ""))


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True, default=str))
