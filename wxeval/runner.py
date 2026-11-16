"""Build the sites, run each system on each site instance and store raw outputs."""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .fetch import Fetcher
from .llm import Claude, DiskCache, Ledger
from .schemas import SCHEMAS
from .sites.render import SiteInstance, build_site
from .sites.server import SiteServer
from .sites.variants import VARIANTS
from .systems.base import Context, System, Task

DEV_SEED = 0


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
    with SiteServer(build / "www") as server, Fetcher(fetch_mode) as fetcher:
        for system in systems:
            ctx = Context(fetcher, ledger, claude if system.uses_llm else None,
                          build / "work" / system.name)
            ctx.workdir.mkdir(parents=True, exist_ok=True)
            for kind in kinds:
                dev = Task(kind, f"{server.base_url}/{kind}/base/s{DEV_SEED}/list/page-1.html",
                           SCHEMAS[kind])
                fetcher.reset_stats()
                ledger.take_usage()
                system.prepare(dev, ctx)
                prep_fetch, prep_llm = fetcher.reset_stats(), ledger.take_usage()
                _write(results / "raw" / system.name / kind / "prepare.json", {
                    "fetch": asdict(prep_fetch), "llm": asdict(prep_llm)})
                for site in [s for s in sites if s.kind == kind]:
                    task = Task(kind, server.base_url + site.start_path, SCHEMAS[kind])
                    error = None
                    start = time.perf_counter()
                    try:
                        records = system.extract(task, ctx)
                    except Exception as exc:
                        if type(exc).__name__ == "BudgetExceeded":
                            raise
                        records = []
                        error = "".join(traceback.format_exception_only(exc)).strip()
                    seconds = time.perf_counter() - start
                    fetch, usage = fetcher.reset_stats(), ledger.take_usage()
                    out = results / "raw" / system.name / kind / site.variant.name / (
                        f"s{site.seed}.json")
                    _write(out, {
                        "system": system.name, "kind": kind, "variant": site.variant.name,
                        "seed": site.seed, "records": records, "error": error,
                        "wall_seconds": round(seconds, 3),
                        # Nominal time: browser time plus original LLM latency, cache or not.
                        "nominal_seconds": round(fetch.seconds + usage.seconds, 3),
                        "fetch": asdict(fetch), "llm": asdict(usage),
                    })
                    log(f"{system.name:>18} {kind} {site.variant.name:<15} s{site.seed} "
                        f"records={len(records):>3} cost=${usage.cost:.4f}"
                        + (f" ERROR {error[:80]}" if error else ""))


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True, default=str))
