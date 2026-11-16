"""Record matching and the extraction metrics.

A predicted record whose key (sku or ref) equals a ground truth key is matched to that
record. The remaining records are matched one to one with the Hungarian algorithm, using
the share of fields that agree as similarity, and a pair counts only if at least
MATCH_THRESHOLD of the fields agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

from ..schemas import Schema
from .normalize import equal, normalize_record

MATCH_THRESHOLD = 0.5


def similarity(pred: dict[str, Any], gold: dict[str, Any], names: list[str]) -> float:
    considered = 0
    agree = 0
    for name in names:
        p, g = pred.get(name), gold.get(name)
        if p is None and g is None:
            continue
        considered += 1
        if p is not None and g is not None and equal(p, g):
            agree += 1
    return agree / considered if considered else 0.0


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def add(self, other: Counts) -> None:
        self.tp += other.tp
        self.fp += other.fp
        self.fn += other.fn

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


@dataclass
class Result:
    n_gold: int
    n_pred: int
    matched: int
    fields: Counts
    per_field: dict[str, Counts]
    duplicates: int
    twin_total: int
    twin_lost: int
    # gold key -> normalized predicted record, used by the freshness metric
    alignment: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def coverage(self) -> float:
        return self.matched / self.n_gold if self.n_gold else 0.0

    @property
    def record_precision(self) -> float:
        return self.matched / self.n_pred if self.n_pred else 0.0

    @property
    def duplicate_rate(self) -> float:
        return self.duplicates / self.n_pred if self.n_pred else 0.0

    @property
    def merge_rate(self) -> float:
        return self.twin_lost / self.twin_total if self.twin_total else 0.0

    def summary(self) -> dict[str, float | int]:
        return {
            "field_precision": self.fields.precision,
            "field_recall": self.fields.recall,
            "field_f1": self.fields.f1,
            "coverage": self.coverage,
            "record_precision": self.record_precision,
            "duplicate_rate": self.duplicate_rate,
            "merge_rate": self.merge_rate,
            "n_gold": self.n_gold,
            "n_pred": self.n_pred,
            "matched": self.matched,
            "tp": self.fields.tp,
            "fp": self.fields.fp,
            "fn": self.fields.fn,
        }


def evaluate(pred_raw: list[dict[str, Any]], gold_raw: list[dict[str, Any]], schema: Schema,
             key: str, twins: dict[str, int] | None = None,
             page_of: dict[str, int] | None = None, strict: bool = False) -> Result:
    names = schema.names()
    pred = [normalize_record(p, schema, strict) for p in pred_raw if isinstance(p, dict)]
    gold = [normalize_record(g, schema, strict) for g in gold_raw]
    gold_keys = [str(g[key]) for g in gold_raw]

    # Step 1: a prediction with the exact record key matches that record.
    pairs: list[tuple[int, int]] = []
    key_index = {gold[j][key]: j for j in range(len(gold))}
    taken: set[int] = set()
    for i, p in enumerate(pred):
        j = key_index.get(p[key])
        if j is not None and j not in taken:
            pairs.append((i, j))
            taken.add(j)
    # Step 2: records with an unknown or missing key are matched on field agreement. A
    # record that repeats a known key is a claim to be that record, so it is not reused.
    rest_p = [i for i in range(len(pred)) if pred[i][key] not in key_index]
    rest_g = [j for j in range(len(gold)) if j not in taken]
    sim = np.zeros((len(pred), len(gold)))
    for i, p in enumerate(pred):
        for j, g in enumerate(gold):
            sim[i, j] = similarity(p, g, names)
    if rest_p and rest_g:
        sub = sim[np.ix_(rest_p, rest_g)]
        rows, cols = linear_sum_assignment(-sub)
        pairs += [(rest_p[int(r)], rest_g[int(c)]) for r, c in zip(rows, cols, strict=True)
                  if sub[r, c] >= MATCH_THRESHOLD]
    pred_matched = {i for i, _ in pairs}
    gold_matched = {j for _, j in pairs}

    per_field = {n: Counts() for n in names}
    for i, j in pairs:
        for n in names:
            p, g = pred[i][n], gold[j][n]
            c = per_field[n]
            if g is not None and p is not None and equal(p, g):
                c.tp += 1
                continue
            if p is not None:
                c.fp += 1
            if g is not None:
                c.fn += 1
    for i, p in enumerate(pred):
        if i not in pred_matched:
            for n in names:
                if p[n] is not None:
                    per_field[n].fp += 1
    for j, g in enumerate(gold):
        if j not in gold_matched:
            for n in names:
                if g[n] is not None:
                    per_field[n].fn += 1
    total = Counts()
    for c in per_field.values():
        total.add(c)

    # A duplicate is an extra prediction for a record that is already matched: same key,
    # or closest by field agreement.
    duplicates = 0
    for i in range(len(pred)):
        if i in pred_matched or not gold:
            continue
        j = key_index.get(pred[i][key])
        if j is None:
            j = int(np.argmax(sim[i]))
            if sim[i, j] < MATCH_THRESHOLD:
                continue
        if j in gold_matched:
            duplicates += 1

    # Wrong merges: a twin is missing while its sibling was found on a page the system
    # did read. Twins on pages the system never reached are coverage misses, not merges.
    twins = twins or {}
    reached: set[int] | None = None
    if page_of is not None:
        reached = {page_of[gold_keys[j]] for j in gold_matched
                   if gold_keys[j] not in twins and gold_keys[j] in page_of}
    groups: dict[int, list[int]] = {}
    for j, k in enumerate(gold_keys):
        if k in twins:
            groups.setdefault(twins[k], []).append(j)
    twin_total = 0
    twin_lost = 0
    for members in groups.values():
        if not any(j in gold_matched for j in members):
            continue
        for j in members:
            if reached is not None and page_of is not None \
                    and page_of.get(gold_keys[j]) not in reached:
                continue
            twin_total += 1
            twin_lost += int(j not in gold_matched)

    alignment = {gold_keys[j]: pred[i] for i, j in pairs}
    return Result(len(gold), len(pred), len(pairs), total, per_field, duplicates,
                  twin_total, twin_lost, alignment)


def change_set(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]],
               names: list[str]) -> set[tuple[str, str, Any]]:
    changes: set[tuple[str, str, Any]] = set()
    for k in after.keys() - before.keys():
        changes.add((k, "added", None))
    for k in before.keys() - after.keys():
        changes.add((k, "removed", None))
    for k in before.keys() & after.keys():
        for n in names:
            a, b = before[k].get(n), after[k].get(n)
            if not (a is None and b is None) and not (a is not None and b is not None
                                                      and equal(a, b)):
                changes.add((k, n, b))
    return changes


def freshness(v1: Result, v2: Result, gold_v1: list[dict[str, Any]], gold_v2: list[dict[str, Any]],
              schema: Schema, key: str) -> Counts:
    """Precision and recall of the changes a system reports between two runs."""
    names = [n for n in schema.names() if n != key]
    g1 = {str(r[key]): normalize_record(r, schema) for r in gold_v1}
    g2 = {str(r[key]): normalize_record(r, schema) for r in gold_v2}
    truth = change_set(g1, g2, names)
    seen = change_set(v1.alignment, v2.alignment, names)
    # Float values are rounded so set membership is exact.
    truth = {(k, n, round(v, 2) if isinstance(v, float) else v) for k, n, v in truth}
    seen = {(k, n, round(v, 2) if isinstance(v, float) else v) for k, n, v in seen}
    tp = len(truth & seen)
    return Counts(tp=tp, fp=len(seen - truth), fn=len(truth - seen))
