"""The LLM reads every page and returns the records plus the next page link."""

from __future__ import annotations

import json
from typing import Any

from .base import Context, System, Task, crawl
from .clean import simplify

SYSTEM = (
    "You extract structured records from web pages. Return every {record} shown on the "
    "page. Use only values that appear on the page and convert them to the field types. "
    "Use null for a field the page does not show. Set next_page_url to the link of the next "
    "page of results, or null if there is none."
)


def page_schema(record_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "records": {"type": "array", "items": record_schema},
            "next_page_url": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
        "required": ["records", "next_page_url"],
        "additionalProperties": False,
    }


class LLMPerPage(System):
    name = "llm_page"
    uses_llm = True

    def extract(self, task: Task, ctx: Context) -> list[dict[str, Any]]:
        assert ctx.claude is not None
        claude = ctx.claude
        system = SYSTEM.format(record=task.schema.record)
        schema = page_schema(task.schema.json_schema())

        def page(html: str, url: str) -> tuple[list[dict[str, Any]], str | None]:
            user = (f"Fields:\n{task.schema.describe()}\n\nPage URL: {url}\n\n"
                    f"Page HTML:\n{simplify(html)}")
            reply = claude.complete(self.name, system, user, schema)
            data = json.loads(reply.text)
            return data["records"], data["next_page_url"]

        return crawl(task.start_url, ctx, page)
