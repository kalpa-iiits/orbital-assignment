"""HTTP client for the external enrichment provider."""

import json
import random
import time
import urllib.error
import urllib.request
from typing import Any

from .models import Metrics


RETRYABLE_CODES = {"TEMPORARY", "RATE_LIMITED"}


class ProviderError(Exception):
    """A request-level provider or transport failure."""

    def __init__(self, code: str, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after: float | None = None


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
        """Call the provider, retrying request-wide transient failures."""
        last_error = ProviderError("UNKNOWN", "request did not run", False)
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._request(domains)
            except ProviderError as error:
                last_error = error
                if not error.retryable or attempt == self.max_attempts:
                    break
                self.metrics.request_retries += 1
                sleep_before_retry(attempt, error.retry_after)
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
            raise self._http_error(error) from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ProviderError("TRANSPORT", str(error), True) from error

        if not isinstance(body, dict) or body.get("status") != "ok":
            code = (
                body.get("code", "BAD_RESPONSE")
                if isinstance(body, dict)
                else "BAD_RESPONSE"
            )
            raise ProviderError(
                str(code),
                "provider returned a non-success response",
                code in RETRYABLE_CODES,
            )

        results = body.get("results")
        if not isinstance(results, list):
            raise ProviderError(
                "BAD_RESPONSE", "provider response has no results array", False
            )
        return results

    @staticmethod
    def _http_error(error: urllib.error.HTTPError) -> ProviderError:
        body = _read_error_body(error)
        retryable = error.code == 429 or error.code >= 500
        code = str(body.get("code") or f"HTTP_{error.code}")
        failure = ProviderError(
            code, str(body.get("message") or error.reason), retryable
        )
        if error.code == 429:
            try:
                failure.retry_after = float(error.headers.get("Retry-After", ""))
            except (TypeError, ValueError):
                pass
        return failure


def sleep_before_retry(attempt: int, retry_after: float | None = None) -> None:
    """Honor Retry-After, otherwise use capped exponential full jitter."""
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
