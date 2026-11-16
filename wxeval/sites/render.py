"""Render a dataset into a static site for one variant.

Every site is plain files: HTML pages, HTML fragments and JSON, so a basic static
server is enough and the output is byte for byte reproducible.
"""

from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .data import Dataset, evolve, make_dataset
from .variants import Variant

PAGE_SIZE = {"shop": 12, "jobs": 3}
FEATURED = 2

SHOP_CLASSES = ["product-card", "product-name", "product-brand", "product-meta",
                "product-category", "product-color", "product-price", "product-rating",
                "product-stock", "product-sku", "catalog", "row", "featured", "inner"]
JOBS_CLASSES = ["company", "company-name", "company-count", "job-list", "job", "job-title",
                "job-location", "job-salary", "job-remote", "job-posted", "job-ref",
                "employer", "employer-head", "openings", "inner"]


@dataclass
class SiteInstance:
    kind: str
    variant: Variant
    seed: int
    path: str  # URL path prefix, for example /shop/base/s1
    truth: Path

    @property
    def start_path(self) -> str:
        return f"{self.path}/list/page-1.html"


class Classes:
    def __init__(self, names: list[str], hashed: bool, seed: int) -> None:
        self.map = {
            n: ("x" + hashlib.sha1(f"{seed}:{n}".encode()).hexdigest()[:6]) if hashed else n
            for n in names
        }

    def __call__(self, name: str) -> str:
        return self.map[name]


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def wrap(inner: str, depth: int, cls: str) -> str:
    for _ in range(depth):
        inner = f'<div class="{cls}">{inner}</div>'
    return inner


def nice_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def shop_item(r: dict[str, Any], v: Variant, c: Classes) -> str:
    w = v.wrap_depth
    inner = c("inner")
    if v.layout == "a":
        parts = [
            f'<h2 class="{c("product-name")}">{esc(r["name"])}</h2>',
            f'<p class="{c("product-brand")}">{esc(r["brand"])}</p>',
            f'<p class="{c("product-meta")}"><span class="{c("product-category")}">'
            f'{esc(r["category"].title())}</span> | <span class="{c("product-color")}">'
            f'{esc(r["color"])}</span></p>',
            f'<p class="{c("product-price")}">${r["price"]:,.2f}</p>',
        ]
        if r["rating"] is not None:
            parts.append(f'<p class="{c("product-rating")}">Rated {r["rating"]} out of 5 '
                         f'({r["reviews"]} reviews)</p>')
        stock = "In stock" if r["in_stock"] else "Out of stock"
        parts.append(f'<p class="{c("product-stock")}">{stock}</p>')
        parts.append(f'<p class="{c("product-sku")}">SKU: {esc(r["sku"])}</p>')
        body = "".join(wrap(p, w, inner) for p in parts)
        return f'<article class="{c("product-card")}">{body}</article>'
    score = (f'{r["rating"]}/5 ({r["reviews"]})' if r["rating"] is not None
             else "No reviews yet")
    cells = [r["name"], r["brand"], r["category"], r["color"], f'{r["price"]:.2f} USD', score,
             "Available" if r["in_stock"] else "Sold out", r["sku"]]
    tds = "".join(f"<td>{wrap(esc(x), w, inner)}</td>" for x in cells)
    return f'<tr class="{c("row")}">{tds}</tr>'


def shop_container(items_html: str, v: Variant, c: Classes) -> str:
    if v.layout == "a":
        return f'<div id="items" class="grid">{items_html}</div>'
    head = "".join(f"<th>{h}</th>" for h in
                   ["Item", "Maker", "Type", "Finish", "Price", "Score", "Availability",
                    "Code"])
    return (f'<table class="{c("catalog")}"><thead><tr>{head}</tr></thead>'
            f'<tbody id="items">{items_html}</tbody></table>')


def job_salary(r: dict[str, Any], layout: str) -> str | None:
    if r["salary_min"] is None:
        return None if layout == "a" else "n/a"
    if layout == "a":
        return f'${r["salary_min"]:,} to ${r["salary_max"]:,} a year'
    return f'{r["salary_min"] // 1000}k to {r["salary_max"] // 1000}k USD'


def jobs_item(company: str, jobs: list[dict[str, Any]], v: Variant, c: Classes) -> str:
    w = v.wrap_depth
    inner = c("inner")
    if v.layout == "a":
        lis = []
        for r in jobs:
            parts = [f'<h3 class="{c("job-title")}">{esc(r["title"])}</h3>',
                     f'<p class="{c("job-location")}">{esc(r["location"])}</p>']
            salary = job_salary(r, "a")
            if salary:
                parts.append(f'<p class="{c("job-salary")}">{salary}</p>')
            parts.append(f'<p class="{c("job-remote")}">'
                         f'{"Remote" if r["remote"] else "On site"}</p>')
            parts.append(f'<p class="{c("job-posted")}">Posted <time datetime="{r["posted"]}">'
                         f'{nice_date(r["posted"])}</time></p>')
            parts.append(f'<p class="{c("job-ref")}">Ref {esc(r["ref"])}</p>')
            lis.append(f'<li class="{c("job")}">{"".join(wrap(p, w, inner) for p in parts)}</li>')
        head = wrap(f'<h2 class="{c("company-name")}">{esc(company)}</h2>', w, inner)
        count = f'<p class="{c("company-count")}">{len(jobs)} open roles</p>'
        return (f'<section class="{c("company")}">{head}{count}'
                f'<ul class="{c("job-list")}">{"".join(lis)}</ul></section>')
    rows = []
    for r in jobs:
        cells = [r["title"], r["location"], job_salary(r, "b"),
                 "Remote" if r["remote"] else "Office", r["posted"], r["ref"]]
        rows.append("<tr>" + "".join(f"<td>{wrap(esc(x), w, inner)}</td>" for x in cells)
                    + "</tr>")
    head = "".join(f"<th>{h}</th>" for h in ["Role", "City", "Pay", "Mode", "Date", "ID"])
    title = wrap(f"<strong>{esc(company)}</strong> is hiring", w, inner)
    return (f'<div class="{c("employer")}"><div class="{c("employer-head")}">{title}</div>'
            f'<table class="{c("openings")}"><tr>{head}</tr>{"".join(rows)}</table></div>')


def pages_for(ds: Dataset) -> list[list[Any]]:
    size = PAGE_SIZE[ds.kind]
    units: list[Any]
    if ds.kind == "shop":
        units = ds.records
    else:
        by_key = ds.by_key()
        units = [(company, [by_key[r] for r in refs]) for company, refs in ds.groups]
    return [units[i:i + size] for i in range(0, len(units), size)]


def render_units(ds: Dataset, units: list[Any], v: Variant, c: Classes) -> str:
    if ds.kind == "shop":
        return "".join(shop_item(r, v, c) for r in units)
    return "".join(jobs_item(company, jobs, v, c) for company, jobs in units)


def container(ds: Dataset, items_html: str, v: Variant, c: Classes) -> str:
    if ds.kind == "shop":
        return shop_container(items_html, v, c)
    return f'<div id="items">{items_html}</div>'


def featured(ds: Dataset, v: Variant, c: Classes) -> str:
    if ds.kind != "shop":
        return ""
    block = container(ds, render_units(ds, ds.records[:FEATURED], v, c), v, c)
    block = block.replace(' id="items"', "")
    return f'<aside class="{c("featured")}"><h2>Featured</h2>{block}</aside>'


def pager(n: int, total: int, v: Variant) -> str:
    if v.pagination == "numbered":
        links = " ".join(f'<a href="page-{i}.html">{i}</a>' if i != n else f"<b>{i}</b>"
                         for i in range(1, total + 1))
        if n < total:
            links += f' <a rel="next" href="page-{n + 1}.html">Next</a>'
        return f'<nav class="pager">{links}</nav>'
    if v.pagination == "next":
        return (f'<p class="more"><a href="page-{n + 1}.html">Next page &rarr;</a></p>'
                if n < total else "")
    return ""


LOAD_MORE_JS = """
<script>
(function () {
  var next = 2, total = %d, busy = false;
  var btn = document.getElementById('load-more');
  function load() {
    if (busy || next > total) return;
    busy = true;
    fetch('more-' + next + '.html').then(function (r) { return r.text(); }).then(function (t) {
      document.getElementById('items').insertAdjacentHTML('beforeend', t);
      next += 1; busy = false;
      if (next > total && btn) btn.remove();
    });
  }
  %s
})();
</script>
"""

CSR_JS = """
<script>
fetch('../api/page-%d.json').then(function (r) { return r.json(); }).then(function (d) {
  document.getElementById('app').innerHTML = d.html;
});
</script>
"""


def page_html(title: str, body: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f"<title>{esc(title)}</title></head><body>"
            f'<header><a href="page-1.html">Home</a> <span>Synthetic test site</span></header>'
            f'<main>{body}</main><footer>All records on this site are synthetic.</footer>'
            f"</body></html>")


def build_site(kind: str, variant: Variant, seed: int, out: Path) -> SiteInstance:
    ds = make_dataset(kind, seed, variant.missing_rate)
    if variant.data_version == 2:
        ds = evolve(ds, seed)
    path = f"/{kind}/{variant.name}/s{seed}"
    root = out / "www" / kind / variant.name / f"s{seed}"
    (root / "list").mkdir(parents=True, exist_ok=True)
    c = Classes(SHOP_CLASSES if kind == "shop" else JOBS_CLASSES, variant.hashed_classes, seed)
    pages = pages_for(ds)
    total = len(pages)
    title = "Gear Shop" if kind == "shop" else "Job Board"
    heading = f"<h1>{title}</h1>"

    if variant.pagination in ("load_more", "infinite"):
        first = container(ds, render_units(ds, pages[0], variant, c), variant, c)
        if variant.pagination == "load_more":
            trigger = '<button id="load-more" type="button">Load more</button>'
            hook = "if (btn) btn.addEventListener('click', load);"
        else:
            trigger = '<div id="sentinel"></div>'
            hook = ("window.addEventListener('scroll', function () {"
                    " if (window.innerHeight + window.scrollY >= document.body.scrollHeight - 300)"
                    " load(); });")
        body = heading + featured(ds, variant, c) + first + trigger
        body += LOAD_MORE_JS % (total, hook)
        (root / "list" / "page-1.html").write_text(page_html(title, body))
        for i, units in enumerate(pages[1:], start=2):
            (root / "list" / f"more-{i}.html").write_text(render_units(ds, units, variant, c))
    else:
        if variant.render == "csr":
            (root / "api").mkdir(exist_ok=True)
        for i, units in enumerate(pages, start=1):
            inner = (featured(ds, variant, c)
                     + container(ds, render_units(ds, units, variant, c), variant, c)
                     + pager(i, total, variant))
            if variant.render == "csr":
                (root / "api" / f"page-{i}.json").write_text(json.dumps({"html": inner}))
                body = heading + '<div id="app">Loading...</div>' + CSR_JS % i
            else:
                body = heading + inner
            (root / "list" / f"page-{i}.html").write_text(page_html(f"{title} page {i}", body))

    truth = out / "truth" / kind / variant.name / f"s{seed}.json"
    truth.parent.mkdir(parents=True, exist_ok=True)
    truth.write_text(json.dumps({
        "kind": kind, "variant": variant.name, "seed": seed, "key": ds.key_field,
        "records": ds.records, "twins": ds.twins, "pages": total,
    }, indent=1, sort_keys=True))
    return SiteInstance(kind, variant, seed, path, truth)
