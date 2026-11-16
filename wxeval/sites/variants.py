from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Variant:
    name: str
    # numbered: page links; next: only a "Next" link; load_more: button that fetches more;
    # infinite: more items load on scroll.
    pagination: str = "numbered"
    # ssr: items in the HTML; csr: items inserted by JavaScript after load.
    render: str = "ssr"
    layout: str = "a"
    hashed_classes: bool = False
    wrap_depth: int = 0
    missing_rate: float | None = None
    data_version: int = 1
    # What part of the robustness suite this variant belongs to.
    group: str = "base"


BASE = Variant("base")

VARIANTS: dict[str, Variant] = {
    v.name: v
    for v in [
        BASE,
        replace(BASE, name="next_only", pagination="next", group="pagination"),
        replace(BASE, name="load_more", pagination="load_more", group="pagination"),
        replace(BASE, name="infinite", pagination="infinite", group="pagination"),
        replace(BASE, name="csr", render="csr", group="rendering"),
        replace(BASE, name="layout_b", layout="b", group="layout"),
        replace(BASE, name="hashed_classes", hashed_classes=True, group="layout"),
        replace(BASE, name="deep_wrap", wrap_depth=3, group="layout"),
        replace(BASE, name="redesign", layout="b", hashed_classes=True, wrap_depth=3,
                group="layout"),
        replace(BASE, name="sparse", missing_rate=0.45, group="data"),
        replace(BASE, name="fresh_v2", data_version=2, group="freshness"),
    ]
}

SMOKE_VARIANTS = ["base", "next_only", "layout_b"]
