from wxeval.sites.data import evolve, make_jobs, make_shop
from wxeval.sites.render import build_site
from wxeval.sites.variants import VARIANTS


def test_datasets_are_seeded():
    assert make_shop(1).records == make_shop(1).records
    assert make_shop(1).records != make_shop(2).records
    assert make_jobs(1).records == make_jobs(1).records


def test_keys_unique():
    for ds in (make_shop(4), make_jobs(4), evolve(make_shop(4), 4)):
        keys = [r[ds.key_field] for r in ds.records]
        assert len(keys) == len(set(keys))


def test_sparse_only_changes_missing_fields():
    a = make_shop(5, missing_rate=0.15).records
    b = make_shop(5, missing_rate=0.45).records
    assert [r["sku"] for r in a] == [r["sku"] for r in b]
    assert sum(r["rating"] is None for r in b) > sum(r["rating"] is None for r in a)


def test_every_variant_builds(tmp_path):
    for kind in ("shop", "jobs"):
        for v in VARIANTS.values():
            site = build_site(kind, v, 1, tmp_path)
            assert site.truth.exists()
            page = tmp_path / "www" / kind / v.name / "s1" / "list" / "page-1.html"
            assert page.exists()


def test_records_appear_in_html(tmp_path):
    site = build_site("shop", VARIANTS["base"], 2, tmp_path)
    html = "".join(p.read_text() for p in (tmp_path / "www/shop/base/s2/list").glob("*.html"))
    for rec in make_shop(2).records:
        assert rec["sku"] in html
    assert site.start_path.endswith("page-1.html")


def test_next_link_moves_to_current_port():
    from wxeval.systems.base import local_url

    cur = "http://127.0.0.1:8781/shop/base/s1/list/page-1.html"
    assert local_url(cur, "page-2.html").endswith(":8781/shop/base/s1/list/page-2.html")
    old = "http://127.0.0.1:8773/shop/base/s1/list/page-2.html"
    assert local_url(cur, old) == "http://127.0.0.1:8781/shop/base/s1/list/page-2.html"
    assert local_url(cur, "https://example.com/x") == "https://example.com/x"
