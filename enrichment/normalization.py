"""Convert inconsistent provider values into a predictable output shape."""

import re
from typing import Any


DOMAIN_RE = re.compile(
    r"^(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)


def normalize_domain(value: str) -> str:
    domain = value.strip().lower().rstrip(".")
    try:
        return domain.encode("idna").decode("ascii")
    except UnicodeError:
        return domain


def is_valid_domain(domain: str) -> bool:
    return bool(DOMAIN_RE.fullmatch(domain))


def normalize_data(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": data.get("name"),
        "employee_count": _normalize_employee_count(data.get("employeeCount")),
        "industries": _normalize_industries(data.get("industry")),
        "location": _normalize_location(data.get("location")),
        "founded_year": data.get("foundedYear"),
        "annual_revenue_usd": data.get("annualRevenueUsd"),
    }


def _normalize_employee_count(value: Any) -> dict[str, Any]:
    if isinstance(value, int) and not isinstance(value, bool):
        return {"kind": "exact", "value": value}

    if isinstance(value, str) and value.replace(",", "").isdigit():
        return {"kind": "exact", "value": int(value.replace(",", ""))}

    if isinstance(value, str) and re.fullmatch(r"[\d,]+-[\d,]+", value):
        low, high = value.split("-", 1)
        return {
            "kind": "range",
            "min": int(low.replace(",", "")),
            "max": int(high.replace(",", "")),
        }

    return {"kind": "unknown"}


def _normalize_industries(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [value]
    return []


def _normalize_location(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {"city": value.get("city"), "country": value.get("country")}
    if isinstance(value, str):
        return {"city": value, "country": None}
    return {"city": None, "country": None}
