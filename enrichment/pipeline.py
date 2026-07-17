"""Streaming CSV-to-JSONL enrichment workflow."""

import csv
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Iterator

from .models import InputRow, Metrics, PipelineConfig
from .normalization import is_valid_domain, normalize_data, normalize_domain
from .provider import (
    RETRYABLE_CODES,
    ProviderClient,
    ProviderError,
    sleep_before_retry,
)


class EnrichmentPipeline:
    """Read input, enrich valid domains, and publish every row's outcome."""

    def __init__(
        self,
        config: PipelineConfig,
        client: ProviderClient | None = None,
    ) -> None:
        self.config = config
        self.metrics = Metrics()
        self.client = client or ProviderClient(
            config.base_url,
            config.token,
            config.timeout,
            config.max_attempts,
            self.metrics,
        )

    def run(self) -> dict[str, Any]:
        """Execute the pipeline and return its operator-facing summary."""
        counts: Counter[str] = Counter()
        failure_reasons: Counter[str] = Counter()
        started = time.monotonic()
        destination = self.config.output_path
        temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
        destination.parent.mkdir(parents=True, exist_ok=True)

        try:
            with temporary.open("w", encoding="utf-8") as handle:
                rows = read_rows(self.config.input_path, self.config.column)
                for group in chunks(rows, self.config.batch_size):
                    for record in self._process_group(group):
                        counts[record["status"]] += 1
                        if record["status"] == "failed":
                            failure_reasons[record["error"]["code"]] += 1
                        handle.write(
                            json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                            + "\n"
                        )
            os.replace(temporary, destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        return self._build_summary(counts, failure_reasons, started)

    def _process_group(self, group: list[InputRow]) -> list[dict[str, Any]]:
        valid_rows: list[InputRow] = []
        records: list[dict[str, Any]] = []

        for row in group:
            if is_valid_domain(row.domain):
                valid_rows.append(row)
            else:
                records.append(_failure(row, "INVALID_DOMAIN", "invalid domain"))

        records.extend(self.enrich_batch(valid_rows))
        records.sort(key=lambda record: record["row_number"])
        return records

    def enrich_batch(self, rows: list[InputRow]) -> list[dict[str, Any]]:
        """Enrich rows, retrying only retryable per-item failures."""
        output: list[dict[str, Any] | None] = [None] * len(rows)
        pending = list(range(len(rows)))

        for item_attempt in range(1, self.config.max_attempts + 1):
            if not pending:
                break

            requested = [rows[index].domain for index in pending]
            try:
                items = self.client.enrich(requested)
            except ProviderError as error:
                for index in pending:
                    output[index] = _failure(
                        rows[index], error.code, str(error), error.retryable
                    )
                break

            if len(items) != len(pending):
                for index in pending:
                    output[index] = _failure(
                        rows[index],
                        "BAD_RESPONSE",
                        "provider result count did not match request",
                    )
                break

            pending = self._handle_items(
                rows, pending, items, output, item_attempt
            )
            if pending:
                sleep_before_retry(item_attempt)

        return [record for record in output if record is not None]

    def _handle_items(
        self,
        rows: list[InputRow],
        pending: list[int],
        items: list[dict[str, Any]],
        output: list[dict[str, Any] | None],
        item_attempt: int,
    ) -> list[int]:
        retry: list[int] = []

        for index, item in zip(pending, items):
            if not _item_matches_row(item, rows[index]):
                output[index] = _failure(
                    rows[index],
                    "BAD_RESPONSE",
                    "provider result domain did not match request",
                )
                continue

            if item.get("status") == "ok" and isinstance(item.get("data"), dict):
                output[index] = _success(rows[index], item["data"])
                continue

            code = str(item.get("code", "BAD_RESPONSE"))
            retryable = bool(item.get("retryable")) or code in RETRYABLE_CODES
            if retryable and item_attempt < self.config.max_attempts:
                retry.append(index)
                self.metrics.item_retries += 1
            else:
                message = str(item.get("message") or "provider could not enrich domain")
                output[index] = _failure(rows[index], code, message, retryable)

        return retry

    def _build_summary(
        self,
        counts: Counter[str],
        failure_reasons: Counter[str],
        started: float,
    ) -> dict[str, Any]:
        return {
            "input": str(self.config.input_path),
            "output": str(self.config.output_path),
            "total": sum(counts.values()),
            "succeeded": counts["succeeded"],
            "failed": counts["failed"],
            "failure_reasons": dict(sorted(failure_reasons.items())),
            "provider_requests": self.metrics.requests,
            "request_retries": self.metrics.request_retries,
            "item_retries": self.metrics.item_retries,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }


def read_rows(path: Path, column: str) -> Iterator[InputRow]:
    """Stream CSV rows, preserving completely blank records."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        if column not in header:
            found = ", ".join(header) or "no header"
            raise ValueError(f"input must contain a {column!r} column (found: {found})")

        column_index = header.index(column)
        for row in reader:
            raw = row[column_index] if column_index < len(row) else ""
            yield InputRow(reader.line_num, raw, normalize_domain(raw))


def chunks(values: Iterable[InputRow], size: int) -> Iterator[list[InputRow]]:
    chunk: list[InputRow] = []
    for value in values:
        chunk.append(value)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _item_matches_row(item: Any, row: InputRow) -> bool:
    if not isinstance(item, dict):
        return False
    return normalize_domain(str(item.get("domain", ""))) == row.domain


def _success(row: InputRow, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "row_number": row.row_number,
        "input_domain": row.input_domain,
        "domain": row.domain,
        "status": "succeeded",
        "data": normalize_data(data),
        "provider_data": data,
    }


def _failure(
    row: InputRow,
    code: str,
    message: str,
    retryable: bool = False,
) -> dict[str, Any]:
    return {
        "row_number": row.row_number,
        "input_domain": row.input_domain,
        "domain": row.domain or None,
        "status": "failed",
        "error": {"code": code, "message": message, "retryable": retryable},
    }
