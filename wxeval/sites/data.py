"""Seeded synthetic records for the shop and jobs sites.

All names are made up from small word lists. No real products, companies or people.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

BRANDS = ["Norvik", "Altura", "Kestrel", "Pellam", "Bramble", "Oduna", "Tessin", "Valko",
          "Quorra", "Lumen", "Harrow", "Mistral Works"]
ADJECTIVES = ["Compact", "Classic", "Ultra", "Smart", "Pro", "Eco", "Travel", "Studio",
              "Daily", "Silent", "Rapid", "Soft"]
NOUNS = {
    "kitchen": ["Kettle", "Blender", "Toaster", "Coffee Grinder", "Rice Cooker"],
    "audio": ["Headphones", "Speaker", "Earbuds", "Turntable", "Soundbar"],
    "outdoor": ["Tent", "Backpack", "Lantern", "Sleeping Bag", "Water Filter"],
    "office": ["Desk Lamp", "Monitor Arm", "Keyboard", "Notebook Stand", "Chair Cushion"],
}
COLORS = ["Black", "White", "Red", "Blue", "Green", "Grey", "Sand", "Olive"]

COMPANY_A = ["Blue", "North", "Silver", "Bright", "Iron", "Cedar", "Harbor", "Summit",
             "Quiet", "Atlas", "Copper", "Maple"]
COMPANY_B = ["Labs", "Systems", "Analytics", "Logistics", "Health", "Robotics", "Foods",
             "Energy", "Media", "Finance"]
ROLES = ["Data Engineer", "Backend Developer", "Product Designer", "QA Engineer",
         "Data Analyst", "ML Engineer", "Support Specialist", "Frontend Developer",
         "Site Reliability Engineer", "Technical Writer"]
LEVELS = ["", "Senior ", "Junior ", "Lead "]
CITIES = ["Istanbul", "Berlin", "Lisbon", "Austin", "Toronto", "Warsaw", "Madrid", "Dublin"]

BASE_DATE = date(2026, 10, 1)


@dataclass
class Dataset:
    kind: str
    records: list[dict[str, Any]]
    # Maps record key to twin group id for records that are near copies of each other.
    twins: dict[str, int] = field(default_factory=dict)
    # Jobs only: company order and which jobs belong to which company.
    groups: list[tuple[str, list[str]]] = field(default_factory=list)

    @property
    def key_field(self) -> str:
        return "sku" if self.kind == "shop" else "ref"

    def by_key(self) -> dict[str, dict[str, Any]]:
        return {r[self.key_field]: r for r in self.records}


def _unique_code(rng: random.Random, prefix: str, used: set[str]) -> str:
    while True:
        code = f"{prefix}-{rng.randint(10000, 99999)}"
        if code not in used:
            used.add(code)
            return code


def _product(rng: random.Random, used: set[str], missing_rate: float) -> dict[str, Any]:
    category = rng.choice(sorted(NOUNS))
    brand = rng.choice(BRANDS)
    name = f"{brand} {rng.choice(ADJECTIVES)} {rng.choice(NOUNS[category])}"
    # Draw every value first so the missing rate does not shift the random stream.
    rating = round(rng.uniform(2.5, 5.0), 1)
    reviews = rng.randint(1, 2500)
    has_rating = rng.random() >= missing_rate
    return {
        "sku": _unique_code(rng, "SKU", used),
        "name": name,
        "brand": brand,
        "category": category,
        "color": rng.choice(COLORS),
        "price": round(rng.uniform(9, 400), 2),
        "rating": rating if has_rating else None,
        "reviews": reviews if has_rating else None,
        "in_stock": rng.random() < 0.8,
    }


def make_shop(seed: int, n: int = 60, twin_share: float = 0.15,
              missing_rate: float = 0.15) -> Dataset:
    rng = random.Random(f"shop-{seed}")
    used: set[str] = set()
    units: list[list[dict[str, Any]]] = []
    twins: dict[str, int] = {}
    group = 0
    count = 0
    while count < n:
        rec = _product(rng, used, missing_rate)
        unit = [rec]
        if count + 1 < n and rng.random() < twin_share:
            # Same product name in another color: a distinct record that looks like a copy.
            twin = dict(rec)
            twin["sku"] = _unique_code(rng, "SKU", used)
            twin["color"] = rng.choice([c for c in COLORS if c != rec["color"]])
            twin["price"] = round(rec["price"] * rng.choice([1.0, 1.0, 1.05, 0.95]), 2)
            twin["in_stock"] = rng.random() < 0.8
            unit.append(twin)
            twins[rec["sku"]] = group
            twins[twin["sku"]] = group
            group += 1
        units.append(unit)
        count += len(unit)
    # Shuffle whole units so twins stay next to each other, as color variants usually do.
    rng.shuffle(units)
    records = [r for unit in units for r in unit]
    return Dataset("shop", records, twins)


def _job(rng: random.Random, used: set[str], company: str, missing_rate: float,
         role: str | None = None) -> dict[str, Any]:
    role = role or f"{rng.choice(LEVELS)}{rng.choice(ROLES)}"
    low = rng.randrange(40, 160, 5) * 1000
    high = low + rng.randrange(10, 50, 5) * 1000
    has_salary = rng.random() >= missing_rate
    return {
        "ref": _unique_code(rng, "JOB", used),
        "title": role,
        "company": company,
        "location": rng.choice(CITIES),
        "salary_min": low if has_salary else None,
        "salary_max": high if has_salary else None,
        "remote": rng.random() < 0.4,
        "posted": (BASE_DATE + timedelta(days=rng.randint(0, 40))).isoformat(),
    }


def make_jobs(seed: int, n_companies: int = 12, twin_share: float = 0.15,
              missing_rate: float = 0.2) -> Dataset:
    rng = random.Random(f"jobs-{seed}")
    used: set[str] = set()
    names: list[str] = []
    while len(names) < n_companies:
        name = f"{rng.choice(COMPANY_A)} {rng.choice(COMPANY_B)}"
        if name not in names:
            names.append(name)
    records: list[dict[str, Any]] = []
    twins: dict[str, int] = {}
    groups: list[tuple[str, list[str]]] = []
    group = 0
    for company in names:
        refs: list[str] = []
        for _ in range(rng.randint(2, 5)):
            job = _job(rng, used, company, missing_rate)
            records.append(job)
            refs.append(job["ref"])
            if rng.random() < twin_share:
                # Same role at the same company, different city.
                twin = _job(rng, used, company, missing_rate, role=job["title"])
                twin["location"] = rng.choice([c for c in CITIES if c != job["location"]])
                records.append(twin)
                refs.append(twin["ref"])
                twins[job["ref"]] = group
                twins[twin["ref"]] = group
                group += 1
        groups.append((company, refs))
    return Dataset("jobs", records, twins, groups)


def evolve(ds: Dataset, seed: int, change_share: float = 0.2, drop_share: float = 0.1,
           add_share: float = 0.1) -> Dataset:
    """Second version of a dataset for the freshness test: edits, removals and additions."""
    rng = random.Random(f"evolve-{ds.kind}-{seed}")
    used = {r[ds.key_field] for r in ds.records}
    keep = [dict(r) for r in ds.records if rng.random() >= drop_share]
    for rec in keep:
        if rng.random() < change_share:
            if ds.kind == "shop":
                rec["price"] = round(rec["price"] * rng.choice([0.8, 0.9, 1.1, 1.25]), 2)
                rec["in_stock"] = not rec["in_stock"]
            elif rec["salary_min"] is not None:
                bump = rng.choice([5000, 10000])
                rec["salary_min"] += bump
                rec["salary_max"] += bump
            else:
                rec["remote"] = not rec["remote"]
    n_add = max(1, round(len(ds.records) * add_share))
    if ds.kind == "shop":
        for _ in range(n_add):
            keep.insert(rng.randrange(len(keep) + 1), _product(rng, used, 0.15))
        kept = {r["sku"] for r in keep}
        twins = {k: g for k, g in ds.twins.items() if k in kept}
        return Dataset("shop", keep, _prune_twins(twins))
    by_company: dict[str, list[str]] = {c: [] for c, _ in ds.groups}
    kept_refs = {r["ref"] for r in keep}
    for company, refs in ds.groups:
        by_company[company] = [r for r in refs if r in kept_refs]
    for _ in range(n_add):
        company = rng.choice([c for c, _ in ds.groups])
        job = _job(rng, used, company, 0.2)
        keep.append(job)
        by_company[company].append(job["ref"])
    groups = [(c, by_company[c]) for c, _ in ds.groups if by_company[c]]
    twins = {k: g for k, g in ds.twins.items() if k in {r["ref"] for r in keep}}
    return Dataset("jobs", keep, _prune_twins(twins), groups)


def _prune_twins(twins: dict[str, int]) -> dict[str, int]:
    sizes: dict[int, int] = {}
    for g in twins.values():
        sizes[g] = sizes.get(g, 0) + 1
    return {k: g for k, g in twins.items() if sizes[g] > 1}


def make_dataset(kind: str, seed: int, missing_rate: float | None = None) -> Dataset:
    if kind == "shop":
        return make_shop(seed, missing_rate=0.15 if missing_rate is None else missing_rate)
    if kind == "jobs":
        return make_jobs(seed, missing_rate=0.2 if missing_rate is None else missing_rate)
    raise ValueError(kind)
