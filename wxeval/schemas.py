from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    name: str
    type: str  # string, number, integer, boolean, date
    description: str
    nullable: bool = False


@dataclass(frozen=True)
class Schema:
    record: str
    fields: tuple[Field, ...]

    def names(self) -> list[str]:
        return [f.name for f in self.fields]

    def json_schema(self) -> dict:
        types = {"string": "string", "number": "number", "integer": "integer",
                 "boolean": "boolean", "date": "string"}
        props = {}
        for f in self.fields:
            t = {"type": types[f.type]}
            props[f.name] = {"anyOf": [t, {"type": "null"}]} if f.nullable else t
        return {"type": "object", "properties": props, "required": self.names(),
                "additionalProperties": False}

    def describe(self) -> str:
        lines = []
        for f in self.fields:
            null = " (null if not shown)" if f.nullable else ""
            lines.append(f"- {f.name} ({f.type}): {f.description}{null}")
        return "\n".join(lines)


SHOP = Schema("product", (
    Field("sku", "string", "product code, for example SKU-12345"),
    Field("name", "string", "full product name"),
    Field("brand", "string", "brand or maker"),
    Field("category", "string", "product category in lower case, for example kitchen"),
    Field("color", "string", "color or finish"),
    Field("price", "number", "price in USD as a number"),
    Field("rating", "number", "average rating out of 5", nullable=True),
    Field("reviews", "integer", "number of reviews", nullable=True),
    Field("in_stock", "boolean", "true if the product is available"),
))

JOBS = Schema("job posting", (
    Field("ref", "string", "job reference code, for example JOB-12345"),
    Field("title", "string", "job title"),
    Field("company", "string", "name of the hiring company"),
    Field("location", "string", "city"),
    Field("salary_min", "integer", "lower end of the yearly salary in USD", nullable=True),
    Field("salary_max", "integer", "upper end of the yearly salary in USD", nullable=True),
    Field("remote", "boolean", "true if the job is remote"),
    Field("posted", "date", "date the job was posted, as YYYY-MM-DD"),
))

SCHEMAS = {"shop": SHOP, "jobs": JOBS}
