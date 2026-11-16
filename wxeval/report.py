"""Score raw outputs against ground truth and write the result tables."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .eval.metrics import Counts, evaluate, freshness
from .eval.stats import bootstrap_ci, paired_bootstrap
from .schemas import SCHEMAS
from .sites.variants import VARIANTS


def load_truth(build: Path, kind: str, variant: str, seed: int) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (build / "truth" / kind / variant / f"s{seed}.json").read_text())
    return data


def score_runs(raw: Path, build: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(raw.glob("*/*/*/s*.json")):
        run = json.loads(path.read_text())
        truth = load_truth(build, run["kind"], run["variant"], run["seed"])
        schema = SCHEMAS[run["kind"]]
        res = evaluate(run["records"], truth["records"], schema, truth["key"], truth["twins"])
        llm = run["llm"]
        row = {
            "system": run["system"], "kind": run["kind"], "variant": run["variant"],
            "group": VARIANTS[run["variant"]].group, "seed": run["seed"],
            **res.summary(),
            "requests": run["fetch"]["requests"], "pages": run["fetch"]["pages"],
            "llm_calls": llm["calls"], "input_tokens": llm["input_tokens"],
            "output_tokens": llm["output_tokens"], "cost_usd": llm["cost"],
            "seconds": run["nominal_seconds"], "error": run["error"] or "",
        }
        rows.append(row)
    return rows


def score_freshness(raw: Path, build: Path) -> list[dict[str, Any]]:
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


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({k: round(v, 4) if isinstance(v, float) else v for k, v in r.items()})


def fmt(mean: float, lo: float, hi: float, digits: int = 2) -> str:
    return f"{mean:.{digits}f} [{lo:.{digits}f}, {hi:.{digits}f}]"


def summarize(rows: list[dict[str, Any]], fresh: list[dict[str, Any]],
              prep: dict[str, dict[str, float]]) -> tuple[str, list[dict[str, Any]]]:
    """Main table. Means over site instances (kind x seed), 95% bootstrap intervals."""
    by_sys: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_sys[r["system"]].append(r)
    fresh_by: dict[str, list[float]] = defaultdict(list)
    for r in fresh:
        fresh_by[r["system"]].append(r["change_f1"])

    table = []
    lines = ["| System | Field F1 (base) | Coverage (pagination + JS) | Field F1 after "
             "redesign | Duplicate rate | Merge rate | Change F1 | USD per 1k records | "
             "Requests per record | Seconds per record |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for system in sorted(by_sys):
        rs = by_sys[system]
        base = [r["field_f1"] for r in rs if r["variant"] == "base"]
        cov = [r["coverage"] for r in rs if r["group"] in ("pagination", "rendering")]
        redesign = [r["field_f1"] for r in rs if r["variant"] == "redesign"]
        dup = [r["duplicate_rate"] for r in rs]
        merge = [r["merge_rate"] for r in rs if r["variant"] != "fresh_v2"]
        matched = sum(r["matched"] for r in rs)
        cost = sum(r["cost_usd"] for r in rs) + prep.get(system, {}).get("cost", 0.0)
        reqs = sum(r["requests"] for r in rs)
        secs = sum(r["seconds"] for r in rs) + prep.get(system, {}).get("seconds", 0.0)
        per_k = 1000 * cost / matched if matched else float("nan")
        row: dict[str, Any] = {
            "system": system,
            "base_f1": bootstrap_ci(base), "coverage": bootstrap_ci(cov),
            "redesign_f1": bootstrap_ci(redesign), "duplicate_rate": bootstrap_ci(dup),
            "merge_rate": bootstrap_ci(merge), "change_f1": bootstrap_ci(fresh_by[system]),
            "usd_per_1k": per_k, "requests_per_record": reqs / matched if matched else 0,
            "seconds_per_record": secs / matched if matched else 0,
        }
        table.append(row)
        lines.append(
            f"| {system} | {fmt(*row['base_f1'])} | {fmt(*row['coverage'])} | "
            f"{fmt(*row['redesign_f1'])} | {fmt(*row['duplicate_rate'], digits=3)} | "
            f"{fmt(*row['merge_rate'], digits=3)} | {fmt(*row['change_f1'])} | "
            f"{per_k:.3f} | {row['requests_per_record']:.2f} | "
            f"{row['seconds_per_record']:.3f} |")
    return "\n".join(lines) + "\n", table


def variant_table(rows: list[dict[str, Any]]) -> tuple[str, dict[str, dict[str, float]]]:
    systems = sorted({r["system"] for r in rows})
    variants = [v for v in VARIANTS if any(r["variant"] == v for r in rows)]
    grid: dict[str, dict[str, float]] = {s: {} for s in systems}
    head = "| Variant | " + " | ".join(systems) + " |"
    lines = [head, "|---" * (len(systems) + 1) + "|"]
    for v in variants:
        cells = []
        for s in systems:
            vals = [r["field_f1"] for r in rows if r["system"] == s and r["variant"] == v]
            mean = sum(vals) / len(vals) if vals else float("nan")
            grid[s][v] = mean
            cells.append(f"{mean:.2f}")
        lines.append(f"| {v} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n", grid


def per_field_table(rows: list[dict[str, Any]], raw: Path, build: Path) -> str:
    del rows
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


def paired_tests(rows: list[dict[str, Any]], pairs: list[tuple[str, str]],
                 variants: list[str]) -> str:
    lines = ["| Comparison | Variants | Mean F1 difference | 95% CI | p |",
             "|---|---|---|---|---|"]
    for a, b in pairs:
        key = {(r["kind"], r["variant"], r["seed"]): r["field_f1"]
               for r in rows if r["system"] == a and r["variant"] in variants}
        other = {(r["kind"], r["variant"], r["seed"]): r["field_f1"]
                 for r in rows if r["system"] == b and r["variant"] in variants}
        common = sorted(key.keys() & other.keys())
        if not common:
            continue
        d, lo, hi, p = paired_bootstrap([key[k] for k in common], [other[k] for k in common])
        lines.append(f"| {a} minus {b} | {', '.join(variants)} | {d:+.3f} | "
                     f"[{lo:+.3f}, {hi:+.3f}] | {p:.3f} |")
    return "\n".join(lines) + "\n"


def load_prepare(raw: Path) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = defaultdict(lambda: {"cost": 0.0, "seconds": 0.0})
    for path in raw.glob("*/*/prepare.json"):
        data = json.loads(path.read_text())
        system = path.parent.parent.name
        out[system]["cost"] += data["llm"]["cost"]
        out[system]["seconds"] += data["llm"]["seconds"] + data["fetch"]["seconds"]
    return out
