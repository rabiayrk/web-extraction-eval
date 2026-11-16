import random

import pytest

from wxeval.eval.metrics import Counts, change_set, evaluate, freshness
from wxeval.eval.normalize import normalize
from wxeval.eval.stats import bootstrap_ci, paired_bootstrap
from wxeval.schemas import SHOP
from wxeval.sites.data import evolve, make_shop

GOLD = make_shop(7).records


def test_perfect_prediction():
    r = evaluate(GOLD, GOLD, SHOP, "sku")
    assert r.fields.f1 == 1.0
    assert r.coverage == 1.0
    assert r.duplicate_rate == 0.0


def test_empty_prediction():
    r = evaluate([], GOLD, SHOP, "sku")
    assert r.fields.f1 == 0.0
    assert r.coverage == 0.0


def test_counts_by_hand():
    gold = [{"sku": "A", "name": "x", "brand": "b", "category": "c", "color": "red",
             "price": 1.0, "rating": None, "reviews": None, "in_stock": True}]
    pred = [{"sku": "A", "name": "x", "brand": "b", "category": "c", "color": "blue",
             "price": 1.0, "rating": 4.0, "reviews": None, "in_stock": True}]
    r = evaluate(pred, gold, SHOP, "sku")
    # 6 fields right, color wrong (1 fp + 1 fn), rating invented (1 fp).
    assert (r.fields.tp, r.fields.fp, r.fields.fn) == (6, 2, 1)
    assert r.fields.precision == pytest.approx(6 / 8)
    assert r.fields.recall == pytest.approx(6 / 7)


def test_duplicates_are_counted():
    r = evaluate(GOLD + GOLD[:5], GOLD, SHOP, "sku")
    assert r.duplicates == 5
    assert r.coverage == 1.0
    assert r.fields.precision < 1.0


def test_dropping_a_twin_counts_as_merge():
    ds = make_shop(7)
    twin_keys = [k for k in ds.twins]
    assert twin_keys
    dropped = twin_keys[0]
    pred = [r for r in ds.records if r["sku"] != dropped]
    r = evaluate(pred, ds.records, SHOP, "sku", ds.twins)
    assert r.twin_lost == 1
    assert r.merge_rate == pytest.approx(1 / len(twin_keys))


def test_shuffled_values_score_low():
    rng = random.Random(0)
    shuffled = [dict(r) for r in GOLD]
    for name in SHOP.names():
        col = [r[name] for r in shuffled]
        rng.shuffle(col)
        for r, v in zip(shuffled, col, strict=True):
            r[name] = v
    r = evaluate(shuffled, GOLD, SHOP, "sku")
    assert r.fields.f1 < 0.4


def test_normalize_formats():
    assert normalize("$1,234.50", "number") == 1234.5
    assert normalize("80k", "integer") == 80000
    assert normalize("Sold out", "boolean") is False
    assert normalize("Oct 2, 2026", "date") == "2026-10-02"
    assert normalize("  Kitchen ", "string") == "kitchen"
    assert normalize("n/a", "integer") is None


def test_freshness_perfect_and_blind():
    ds1 = make_shop(3)
    ds2 = evolve(ds1, 3)
    r1 = evaluate(ds1.records, ds1.records, SHOP, "sku")
    r2 = evaluate(ds2.records, ds2.records, SHOP, "sku")
    perfect = freshness(r1, r2, ds1.records, ds2.records, SHOP, "sku")
    assert perfect.f1 == 1.0
    # A system that returns the old data again sees no changes at all.
    stale = evaluate(ds1.records, ds2.records, SHOP, "sku")
    blind = freshness(r1, stale, ds1.records, ds2.records, SHOP, "sku")
    assert blind.recall < 0.5


def test_change_set():
    before = {"a": {"p": 1.0}, "b": {"p": 2.0}}
    after = {"a": {"p": 1.5}, "c": {"p": 3.0}}
    assert change_set(before, after, ["p"]) == {("a", "p", 1.5), ("b", "removed", None),
                                                ("c", "added", None)}


def test_counts_f1():
    c = Counts(tp=8, fp=2, fn=8)
    assert c.precision == 0.8
    assert c.recall == 0.5
    assert c.f1 == pytest.approx(2 * 0.8 * 0.5 / 1.3)


def test_bootstrap():
    mean, lo, hi = bootstrap_ci([1.0] * 10)
    assert mean == lo == hi == 1.0
    d, lo, hi, p = paired_bootstrap([1.0, 0.9, 0.8, 1.0], [0.5, 0.4, 0.6, 0.5])
    assert d > 0 and lo > 0 and p < 0.05


def _twin_pair(ds):
    groups = {}
    for k, g in ds.twins.items():
        groups.setdefault(g, []).append(k)
    return next(iter(groups.values()))


def test_copying_a_twin_is_a_merge_and_a_duplicate():
    for seed in (1, 2, 3):
        ds = make_shop(seed)
        a, b = _twin_pair(ds)
        by_key = ds.by_key()
        pred = [dict(by_key[a]) if r["sku"] == b else r for r in ds.records]
        r = evaluate(pred, ds.records, SHOP, "sku", ds.twins)
        assert r.duplicates == 1
        assert r.twin_lost == 1


def test_missing_page_is_not_a_merge(tmp_path):
    from wxeval.sites.render import build_site
    from wxeval.sites.variants import VARIANTS

    site = build_site("shop", VARIANTS["base"], 1, tmp_path)
    import json

    truth = json.loads(site.truth.read_text())
    pred = [r for r in truth["records"] if truth["page_of"][r["sku"]] == 1]
    r = evaluate(pred, truth["records"], SHOP, "sku", truth["twins"], truth["page_of"])
    assert r.twin_lost == 0
    assert r.coverage < 0.5


def test_strict_mode_rejects_text_values():
    gold = [{"sku": "A", "name": "x", "brand": "b", "category": "c", "color": "red",
             "price": 12.5, "rating": None, "reviews": None, "in_stock": True}]
    pred = [dict(gold[0], price="$12.50", in_stock="In stock")]
    assert evaluate(pred, gold, SHOP, "sku").fields.f1 == 1.0
    strict = evaluate(pred, gold, SHOP, "sku", strict=True)
    assert strict.fields.tp == 5
