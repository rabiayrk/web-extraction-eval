"""Score raw outputs against ground truth and write the result tables.

The unit for intervals and tests is a site instance cluster: one (site kind, seed) pair.
All variants of a cluster share the same underlying records, so they are averaged inside
the cluster first and only clusters are resampled.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .eval.metrics import Counts, evaluate, freshness
from .eval.stats import bootstrap_ci, holm, paired_bootstrap, sign_flip_test
from .schemas import SCHEMAS
from .sites.variants import VARIANTS

LAYOUT = ["layout_b", "hashed_classes", "deep_wrap", "redesign"]
LOADING = ["next_only", "load_more", "infinite", "csr"]

Row = dict[str, Any]


def load_truth(build: Path, kind: str, variant: str, seed: int) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (build / "truth" / kind / variant / f"s{seed}.json").read_text())
    return data


def score_runs(raw: Path, build: Path) -> list[Row]:
    rows = []
    for path in sorted(raw.glob("*/*/*/s*.json")):
        run = json.loads(path.read_text())
        truth = load_truth(build, run["kind"], run["variant"], run["seed"])
        schema = SCHEMAS[run["kind"]]
        res = evaluate(run["records"], truth["records"], schema, truth["key"], truth["twins"],
                       truth.get("page_of"))
        strict = evaluate(run["records"], truth["records"], schema, truth["key"], strict=True)
        llm = run["llm"]
        rows.append({
            "system": run["system"], "kind": run["kind"], "variant": run["variant"],
            "group": VARIANTS[run["variant"]].group, "seed": run["seed"],
            **res.summary(), "strict_f1": strict.fields.f1,
            "duplicates": res.duplicates, "twin_total": res.twin_total,
            "twin_lost": res.twin_lost,
            "requests": run["fetch"]["requests"], "pages": run["fetch"]["pages"],
            "llm_calls": llm["calls"], "input_tokens": llm["input_tokens"],
            "output_tokens": llm["output_tokens"], "cost_usd": llm["cost"],
            "seconds": run["nominal_seconds"], "error": run["error"] or "",
            "page_errors": len(run.get("page_errors") or []),
        })
    return rows


def score_freshness(raw: Path, build: Path) -> list[Row]:
    rows = []
    for v2_path in sorted(raw.glob("*/*/fresh_v2/s*.json")):
        v1_path = v2_path.parent.parent / "base" / v2_path.name
        if not v1_path.exists():
            continue
        r1, r2 = json.loads(v1_path.read_text()), json.loads(v2_path.read_text())
        t1 = load_truth(build, r1["kind"], "base", r1["seed"])
        t2 = load_truth(build, r2["kind"], "fresh_v2", r2["seed"])
        schema = SCHEMAS[r1["kind"]]
        e1 = evaluate(r1["records"], t1["records"], schema, t1["key"])
        e2 = evaluate(r2["records"], t2["records"], schema, t2["key"])
        c = freshness(e1, e2, t1["records"], t2["records"], schema, t1["key"])
        rows.append({"system": r1["system"], "kind": r1["kind"], "seed": r1["seed"],
                     "change_precision": c.precision, "change_recall": c.recall,
                     "change_f1": c.f1, "tp": c.tp, "fp": c.fp, "fn": c.fn})
    return rows


def write_csv(path: Path, rows: list[Row]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({k: round(v, 4) if isinstance(v, float) else v for k, v in r.items()})


def fmt(ci: tuple[float, float, float], digits: int = 2) -> str:
    mean, lo, hi = ci
    if mean != mean:
        return "n/a"
    return f"{mean:.{digits}f} [{lo:.{digits}f}, {hi:.{digits}f}]"


def clusters(rows: Iterable[Row], value: Callable[[list[Row]], float | None]) -> list[float]:
    """One value per (kind, seed) cluster; clusters where value returns None are skipped."""
    groups: dict[tuple[str, int], list[Row]] = defaultdict(list)
    for r in rows:
        groups[(r["kind"], r["seed"])].append(r)
    out = []
    for key in sorted(groups):
        v = value(groups[key])
        if v is not None:
            out.append(v)
    return out


def mean_of(field: str) -> Callable[[list[Row]], float | None]:
    def f(rs: list[Row]) -> float | None:
        return sum(r[field] for r in rs) / len(rs) if rs else None
    return f


def ratio(num: str, den: str) -> Callable[[list[Row]], float | None]:
    def f(rs: list[Row]) -> float | None:
        d = sum(r[den] for r in rs)
        return sum(r[num] for r in rs) / d if d else None
    return f


def summarize(rows: list[Row], fresh: list[Row],
              prep: dict[str, dict[str, float]]) -> tuple[str, str]:
    by_sys: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_sys[r["system"]].append(r)
    main = ["| System | Field F1, base | Coverage, loading variants | Field F1, layout "
            "variants | Field F1, redesign | USD per 1k records |",
            "|---|---|---|---|---|---|"]
    extra = ["| System | Field F1, base, strict types | Duplicate rate | Wrong merge rate | "
             "Change F1 (freshness) | Setup USD | Requests per record | Seconds per record |",
             "|---|---|---|---|---|---|---|---|"]
    for system in sorted(by_sys):
        rs = by_sys[system]

        def sel(names: list[str], rs: list[Row] = rs) -> list[Row]:
            return [r for r in rs if r["variant"] in names]

        base = bootstrap_ci(clusters(sel(["base"]), mean_of("field_f1")))
        strict = bootstrap_ci(clusters(sel(["base"]), mean_of("strict_f1")))
        cov = bootstrap_ci(clusters(sel(LOADING), mean_of("coverage")))
        layout = bootstrap_ci(clusters(sel(LAYOUT), mean_of("field_f1")))
        redesign = bootstrap_ci(clusters(sel(["redesign"]), mean_of("field_f1")))
        # Rates only over runs that found something, so empty output does not look clean.
        found = [r for r in rs if r["matched"] > 0]
        dup = bootstrap_ci(clusters(found, ratio("duplicates", "n_pred")))
        merge = bootstrap_ci(clusters(found, ratio("twin_lost", "twin_total")))
        change = bootstrap_ci(clusters([f for f in fresh if f["system"] == system],
                                       mean_of("change_f1")))
        matched = sum(r["matched"] for r in rs)
        cost = sum(r["cost_usd"] for r in rs)
        per_k = 1000 * cost / matched if matched else float("nan")
        setup = prep.get(system, {}).get("cost", 0.0)
        reqs = sum(r["requests"] for r in rs) / matched if matched else float("nan")
        secs = sum(r["seconds"] for r in rs) / matched if matched else float("nan")
        main.append(f"| {system} | {fmt(base)} | {fmt(cov)} | {fmt(layout)} | "
                    f"{fmt(redesign)} | {per_k:.3f} |")
        extra.append(f"| {system} | {fmt(strict)} | {fmt(dup, 3)} | {fmt(merge, 3)} | "
                     f"{fmt(change)} | {setup:.4f} | {reqs:.2f} | {secs:.3f} |")
    return "\n".join(main) + "\n", "\n".join(extra) + "\n"


def variant_table(rows: list[Row],
                  kind: str | None = None) -> tuple[str, dict[str, dict[str, float]]]:
    rows = [r for r in rows if kind is None or r["kind"] == kind]
    systems = sorted({r["system"] for r in rows})
    variants = [v for v in VARIANTS if any(r["variant"] == v for r in rows)]
    grid: dict[str, dict[str, float]] = {s: {} for s in systems}
    lines = ["| Variant | " + " | ".join(systems) + " |", "|---" * (len(systems) + 1) + "|"]
    for v in variants:
        cells = []
        for s in systems:
            vals = [r["field_f1"] for r in rows if r["system"] == s and r["variant"] == v]
            mean = sum(vals) / len(vals) if vals else float("nan")
            grid[s][v] = mean
            cells.append(f"{mean:.2f}")
        lines.append(f"| {v} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n", grid


def per_field_table(raw: Path, build: Path) -> str:
    counts: dict[tuple[str, str, str], Counts] = defaultdict(Counts)
    for path in sorted(raw.glob("*/*/base/s*.json")):
        run = json.loads(path.read_text())
        truth = load_truth(build, run["kind"], "base", run["seed"])
        res = evaluate(run["records"], truth["records"], SCHEMAS[run["kind"]], truth["key"])
        for name, c in res.per_field.items():
            counts[(run["kind"], name, run["system"])].add(c)
    systems = sorted({k[2] for k in counts})
    lines = ["| Site | Field | " + " | ".join(systems) + " |",
             "|---|---" + "|---" * len(systems) + "|"]
    for kind in ("shop", "jobs"):
        for f in SCHEMAS[kind].names():
            cells = [f"{counts[(kind, f, s)].f1:.2f}" if (kind, f, s) in counts else "-"
                     for s in systems]
            lines.append(f"| {kind} | {f} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _cluster_means(rows: list[Row], system: str, variants: list[str]) -> dict[Any, float]:
    groups: dict[tuple[str, int], list[float]] = defaultdict(list)
    for r in rows:
        if r["system"] == system and r["variant"] in variants:
            groups[(r["kind"], r["seed"])].append(r["field_f1"])
    return {k: sum(v) / len(v) for k, v in groups.items()}


def paired_tests(rows: list[Row], pairs: list[tuple[str, str]], variants: list[str]) -> str:
    """Cluster level paired differences, exact sign flip test, Holm adjusted."""
    results = []
    for a, b in pairs:
        va, vb = _cluster_means(rows, a, variants), _cluster_means(rows, b, variants)
        common = sorted(va.keys() & vb.keys())
        if len(common) < 2:
            continue
        xa, xb = [va[k] for k in common], [vb[k] for k in common]
        d, lo, hi, _ = paired_bootstrap(xa, xb)
        p = sign_flip_test([x - y for x, y in zip(xa, xb, strict=True)])
        results.append((a, b, d, lo, hi, p, len(common)))
    adjusted = holm([r[5] for r in results]) if results else []
    lines = ["| Comparison | Mean F1 difference | 95% CI | p (sign flip) | p (Holm) | Clusters |",
             "|---|---|---|---|---|---|"]
    for (a, b, d, lo, hi, p, n), padj in zip(results, adjusted, strict=True):
        lines.append(f"| {a} minus {b} | {d:+.3f} | [{lo:+.3f}, {hi:+.3f}] | {p:.3f} | "
                     f"{padj:.3f} | {n} |")
    return "\n".join(lines) + "\n"


def load_prepare(raw: Path) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = defaultdict(lambda: {"cost": 0.0, "seconds": 0.0})
    for path in raw.glob("*/*/prepare.json"):
        data = json.loads(path.read_text())
        system = path.parent.parent.name
        out[system]["cost"] += data["llm"]["cost"]
        out[system]["seconds"] += data["llm"]["seconds"] + data["fetch"]["seconds"]
    return out
