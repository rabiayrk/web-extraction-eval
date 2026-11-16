"""Hand written scraper with parsel, built by looking at the development site only."""

from __future__ import annotations

import re
from typing import Any

from parsel import Selector

from .base import Context, System, Task, crawl


def _num(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    return float(m.group().replace(",", "")) if m else None


def shop_page(html: str, url: str) -> tuple[list[dict[str, Any]], str | None]:
    sel = Selector(html)
    out = []
    for card in sel.css("#items article.product-card"):
        rating = card.css(".product-rating::text").get()
        m = re.search(r"Rated ([\d.]+) out of 5 \((\d+) reviews\)", rating or "")
        out.append({
            "sku": (card.css(".product-sku::text").get() or "").replace("SKU:", "").strip(),
            "name": card.css(".product-name::text").get(),
            "brand": card.css(".product-brand::text").get(),
            "category": (card.css(".product-category::text").get() or "").lower() or None,
            "color": card.css(".product-color::text").get(),
            "price": _num(card.css(".product-price::text").get()),
            "rating": float(m.group(1)) if m else None,
            "reviews": int(m.group(2)) if m else None,
            "in_stock": (card.css(".product-stock::text").get() or "").strip() == "In stock",
        })
    return out, sel.css("a[rel=next]::attr(href)").get()


def jobs_page(html: str, url: str) -> tuple[list[dict[str, Any]], str | None]:
    sel = Selector(html)
    out = []
    for company in sel.css("#items section.company"):
        name = company.css(".company-name::text").get()
        for job in company.css("li.job"):
            salary = re.findall(r"\$([\d,]+)", job.css(".job-salary::text").get() or "")
            out.append({
                "ref": (job.css(".job-ref::text").get() or "").replace("Ref", "").strip(),
                "title": job.css(".job-title::text").get(),
                "company": name,
                "location": job.css(".job-location::text").get(),
                "salary_min": int(salary[0].replace(",", "")) if salary else None,
                "salary_max": int(salary[1].replace(",", "")) if len(salary) > 1 else None,
                "remote": (job.css(".job-remote::text").get() or "").strip() == "Remote",
                "posted": job.css("time::attr(datetime)").get(),
            })
    return out, sel.css("a[rel=next]::attr(href)").get()


class Handwritten(System):
    name = "handwritten"

    def extract(self, task: Task, ctx: Context) -> list[dict[str, Any]]:
        page_fn = shop_page if task.kind == "shop" else jobs_page
        return crawl(task.start_url, ctx, page_fn, fallback_next=True)
