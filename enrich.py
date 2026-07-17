#!/usr/bin/env python3
"""Stream domains from CSV, enrich them in bounded batches, and write JSONL."""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator


DOMAIN_RE = re.compile(
    r"^(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)
RETRYABLE_CODES = {"TEMPORARY", "RATE_LIMITED"}


class ProviderError(Exception):
    """A request-level provider or transport failure."""

    def __init__(self, code: str, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass
class InputRow:
    row_number: int
    input_domain: str
    domain: str


@dataclass
class Metrics:
    requests: int = 0
    request_retries: int = 0
    item_retries: int = 0


def normalize_domain(value: str) -> str:
    domain = value.strip().lower().rstrip(".")
    try:
        return domain.encode("idna").decode("ascii")
    except UnicodeError:
        return domain


def is_valid_domain(domain: str) -> bool:
    return bool(DOMAIN_RE.fullmatch(domain))


def read_rows(path: Path, column: str) -> Iterator[InputRow]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or column not in reader.fieldnames:
            found = ", ".join(reader.fieldnames or []) or "no header"
            raise ValueError(f"input must contain a {column!r} column (found: {found})")
        for row_number, row in enumerate(reader, start=2):
            raw = row.get(column) or ""
            yield InputRow(row_number, raw, normalize_domain(raw))


def chunks(values: Iterable[InputRow], size: int) -> Iterator[list[InputRow]]:
    chunk: list[InputRow] = []
    for value in values:
        chunk.append(value)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


class ProviderClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float,
        max_attempts: int,
        metrics: Metrics,
    ) -> None:
        self.url = f"{base_url.rstrip('/')}/v1/enrich/batch"
        self.token = token
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.metrics = metrics

    def enrich(self, domains: list[str]) -> list[dict[str, Any]]:
        last_error = ProviderError("UNKNOWN", "request did not run", False)
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._request(domains)
            except ProviderError as error:
                last_error = error
                if not error.retryable or attempt == self.max_attempts:
                    break
                self.metrics.request_retries += 1
                self._sleep(attempt, getattr(error, "retry_after", None))
        raise last_error

    def _request(self, domains: list[str]) -> list[dict[str, Any]]:
        payload = json.dumps({"domains": domains}).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "X-Provider-Version": "2",
            },
        )
        self.metrics.requests += 1
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.load(response)
        except urllib.error.HTTPError as error:
            retryable = error.code == 429 or error.code >= 500
            provider_body = _read_error_body(error)
            code = str(provider_body.get("code") or f"HTTP_{error.code}")
            failure = ProviderError(code, str(provider_body.get("message") or error.reason), retryable)
            if error.code == 429:
                try:
                    failure.retry_after = float(error.headers.get("Retry-After", ""))
                except (TypeError, ValueError):
                    failure.retry_after = None
            raise failure from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ProviderError("TRANSPORT", str(error), True) from error

        if not isinstance(body, dict) or body.get("status") != "ok":
            code = body.get("code", "BAD_RESPONSE") if isinstance(body, dict) else "BAD_RESPONSE"
            raise ProviderError(str(code), "provider returned a non-success response", code in RETRYABLE_CODES)
        results = body.get("results")
        if not isinstance(results, list):
            raise ProviderError("BAD_RESPONSE", "provider response has no results array", False)
        return results

    @staticmethod
    def _sleep(attempt: int, retry_after: float | None) -> None:
        if retry_after is not None:
            time.sleep(max(0.0, retry_after))
            return
        cap = min(8.0, 0.5 * (2 ** (attempt - 1)))
        time.sleep(random.uniform(0.0, cap))


def _read_error_body(error: urllib.error.HTTPError) -> dict[str, Any]:
    try:
        body = json.load(error)
        return body if isinstance(body, dict) else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def normalize_data(data: dict[str, Any]) -> dict[str, Any]:
    employee = data.get("employeeCount")
    employee_count: dict[str, Any]
    if isinstance(employee, int) and not isinstance(employee, bool):
        employee_count = {"kind": "exact", "value": employee}
    elif isinstance(employee, str) and employee.replace(",", "").isdigit():
        employee_count = {"kind": "exact", "value": int(employee.replace(",", ""))}
    elif isinstance(employee, str) and re.fullmatch(r"[\d,]+-[\d,]+", employee):
        low, high = employee.split("-", 1)
        employee_count = {
            "kind": "range",
            "min": int(low.replace(",", "")),
            "max": int(high.replace(",", "")),
        }
    else:
        employee_count = {"kind": "unknown"}

    industry = data.get("industry")
    industries = industry if isinstance(industry, list) else ([industry] if isinstance(industry, str) else [])
    location = data.get("location")
    if isinstance(location, dict):
        normalized_location = {"city": location.get("city"), "country": location.get("country")}
    elif isinstance(location, str):
        normalized_location = {"city": location, "country": None}
    else:
        normalized_location = {"city": None, "country": None}

    return {
        "name": data.get("name"),
        "employee_count": employee_count,
        "industries": industries,
        "location": normalized_location,
        "founded_year": data.get("foundedYear"),
        "annual_revenue_usd": data.get("annualRevenueUsd"),
    }


def failure(row: InputRow, code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    return {
        "row_number": row.row_number,
        "input_domain": row.input_domain,
        "domain": row.domain or None,
        "status": "failed",
        "error": {"code": code, "message": message, "retryable": retryable},
    }


def success(row: InputRow, item: dict[str, Any]) -> dict[str, Any]:
    data = item["data"]
    return {
        "row_number": row.row_number,
        "input_domain": row.input_domain,
        "domain": row.domain,
        "status": "succeeded",
        "data": normalize_data(data),
        "provider_data": data,
    }


def enrich_batch(
    rows: list[InputRow], client: ProviderClient, metrics: Metrics
) -> list[dict[str, Any]]:
    output: list[dict[str, Any] | None] = [None] * len(rows)
    pending = list(range(len(rows)))

    for item_attempt in range(1, client.max_attempts + 1):
        if not pending:
            break
        requested = [rows[index].domain for index in pending]
        try:
            items = client.enrich(requested)
        except ProviderError as error:
            for index in pending:
                output[index] = failure(rows[index], error.code, str(error), error.retryable)
            break

        if len(items) != len(pending):
            for index in pending:
                output[index] = failure(
                    rows[index], "BAD_RESPONSE", "provider result count did not match request"
                )
            break

        retry: list[int] = []
        for index, item in zip(pending, items):
            item_domain = normalize_domain(str(item.get("domain", ""))) if isinstance(item, dict) else ""
            if item_domain != rows[index].domain:
                output[index] = failure(
                    rows[index], "BAD_RESPONSE", "provider result domain did not match request"
                )
                continue
            if isinstance(item, dict) and item.get("status") == "ok" and isinstance(item.get("data"), dict):
                output[index] = success(rows[index], item)
                continue
            code = str(item.get("code", "BAD_RESPONSE")) if isinstance(item, dict) else "BAD_RESPONSE"
            retryable = bool(item.get("retryable")) if isinstance(item, dict) else False
            retryable = retryable or code in RETRYABLE_CODES
            if retryable and item_attempt < client.max_attempts:
                retry.append(index)
                metrics.item_retries += 1
            else:
                message = str(item.get("message") or "provider could not enrich domain") if isinstance(item, dict) else "invalid provider item"
                output[index] = failure(rows[index], code, message, retryable)
        pending = retry
        if pending:
            ProviderClient._sleep(item_attempt, None)

    return [item for item in output if item is not None]


def run(args: argparse.Namespace) -> dict[str, Any]:
    metrics = Metrics()
    client = ProviderClient(args.base_url, args.token, args.timeout, args.max_attempts, metrics)
    counts: Counter[str] = Counter()
    failure_reasons: Counter[str] = Counter()
    started = time.monotonic()
    destination = Path(args.output)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        with temporary.open("w", encoding="utf-8") as handle:
            for group in chunks(read_rows(Path(args.input), args.column), args.batch_size):
                valid: list[InputRow] = []
                records: list[dict[str, Any]] = []
                for row in group:
                    if is_valid_domain(row.domain):
                        valid.append(row)
                    else:
                        records.append(failure(row, "INVALID_DOMAIN", "not a valid company domain"))
                records.extend(enrich_batch(valid, client, metrics))
                records.sort(key=lambda record: record["row_number"])
                for record in records:
                    counts[record["status"]] += 1
                    if record["status"] == "failed":
                        failure_reasons[record["error"]["code"]] += 1
                    handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.replace(temporary, destination)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise

    return {
        "input": str(Path(args.input)),
        "output": str(destination),
        "total": sum(counts.values()),
        "succeeded": counts["succeeded"],
        "failed": counts["failed"],
        "failure_reasons": dict(sorted(failure_reasons.items())),
        "provider_requests": metrics.requests,
        "request_retries": metrics.request_retries,
        "item_retries": metrics.item_retries,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="starter-kit/domains.csv")
    parser.add_argument("--output", default="enriched.jsonl")
    parser.add_argument("--summary", help="also write the run summary as JSON")
    parser.add_argument("--column", default="domain")
    parser.add_argument("--base-url", default=os.getenv("PROVIDER_URL", "http://localhost:4000"))
    parser.add_argument("--token", default=os.getenv("PROVIDER_TOKEN"))
    parser.add_argument("--batch-size", type=int, default=10, choices=range(1, 26), metavar="1..25")
    parser.add_argument("--timeout", type=float, default=6.0)
    parser.add_argument("--max-attempts", type=int, default=4)
    args = parser.parse_args(argv)
    if not args.token:
        parser.error("set PROVIDER_TOKEN or pass --token")
    if args.timeout <= 0 or args.max_attempts < 1:
        parser.error("--timeout must be positive and --max-attempts must be at least 1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        summary = run(args)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    rendered = json.dumps(summary, indent=2, sort_keys=True)
    print(rendered)
    if args.summary:
        Path(args.summary).write_text(rendered + "\n", encoding="utf-8")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
