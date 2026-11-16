"""The LLM writes a scraper once, on the development site. The code then runs on every
test page with no further LLM calls."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .base import Context, System, Task, crawl
from .clean import simplify

RUNNER = Path(__file__).with_name("sandbox_runner.py")
MAX_ATTEMPTS = 3

SYSTEM = (
    "You write Python web scrapers. Write a function extract(html: str, url: str) -> dict "
    "that returns {\"records\": [...], \"next_url\": str | None}. Use only the standard "
    "library and parsel (from parsel import Selector). Each record is a dict with exactly "
    "the listed keys. Convert values to the field types: numbers as int or float, booleans "
    "as True or False, dates as YYYY-MM-DD strings, None when a value is missing. The code "
    "must work on every page of this site, so do not hard code record values. Return only "
    "the code in one ```python block."
)


def run_code(code_path: Path, html: str, url: str, timeout: int = 60) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-I", str(RUNNER), str(code_path)],
        input=json.dumps({"html": html, "url": url}), capture_output=True, text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip().splitlines()[-1] if proc.stderr else "failed")
    data: dict[str, Any] = json.loads(proc.stdout)
    return data


def check(out: dict[str, Any], names: list[str]) -> str | None:
    records = out.get("records")
    if not isinstance(records, list) or not records:
        return "The function returned no records."
    keys = set(records[0]) if isinstance(records[0], dict) else set()
    if keys != set(names):
        return f"Records have keys {sorted(keys)}, expected {sorted(names)}."
    return None


class LLMWrittenCode(System):
    name = "llm_code"
    uses_llm = True

    def __init__(self) -> None:
        self.code: dict[str, Path] = {}

    def prepare(self, dev: Task, ctx: Context) -> None:
        assert ctx.claude is not None
        html = ctx.fetcher.get(dev.start_url)
        page = simplify(html, keep=("href", "datetime", "class", "id", "rel"))
        base = (f"Fields:\n{dev.schema.describe()}\n\nPage URL: {dev.start_url}\n\n"
                f"Page HTML:\n{page}")
        user = base
        path = ctx.workdir / f"{dev.kind}.py"
        for attempt in range(MAX_ATTEMPTS):
            reply = ctx.claude.complete(self.name, SYSTEM, user, max_tokens=16000)
            m = re.search(r"```python\n(.*?)```", reply.text, re.S)
            code = m.group(1) if m else reply.text
            path.write_text(code)
            try:
                problem = check(run_code(path, html, dev.start_url), dev.schema.names())
            except Exception as exc:
                problem = f"Running the code failed: {exc}"
            if problem is None:
                break
            if attempt < MAX_ATTEMPTS - 1:
                user = (f"{base}\n\nYour previous code:\n```python\n{code}```\n\n{problem} "
                        "Fix the code.")
        self.code[dev.kind] = path

    def extract(self, task: Task, ctx: Context) -> list[dict[str, Any]]:
        path = self.code[task.kind]

        def page(html: str, url: str) -> tuple[list[dict[str, Any]], str | None]:
            try:
                out = run_code(path, html, url)
            except (RuntimeError, ValueError, subprocess.TimeoutExpired):
                # Generated code that crashes on a page yields nothing for that page.
                return [], None
            return list(out.get("records") or []), out.get("next_url")

        return crawl(task.start_url, ctx.fetcher, page)
