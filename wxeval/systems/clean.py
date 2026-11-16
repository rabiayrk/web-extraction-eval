from __future__ import annotations

import re

import lxml.html

DROP = ["script", "style", "noscript", "svg", "head", "iframe", "template"]


def simplify(html: str, keep: tuple[str, ...] = ("href", "datetime")) -> str:
    """Shrink a page for an LLM: drop scripts and styles and most attributes."""
    root = lxml.html.fromstring(html)
    for el in root.xpath("//" + " | //".join(DROP)):
        el.drop_tree()
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in list(el.attrib):
            if attr not in keep:
                del el.attrib[attr]
    body = root.find("body")
    text = lxml.html.tostring(body if body is not None else root, encoding="unicode")
    return re.sub(r">\s+<", "><", text).strip()
