import io
import json
import unittest
import urllib.error
from email.message import Message
from unittest.mock import patch

from enrichment.models import Metrics
from enrichment.provider import ProviderClient, ProviderError


def response(body):
    return io.BytesIO(json.dumps(body).encode("utf-8"))


class ProviderClientTests(unittest.TestCase):
    def make_client(self, max_attempts=2):
        self.metrics = Metrics()
        return ProviderClient(
            "http://provider.test",
            "secret",
            timeout=3,
            max_attempts=max_attempts,
            metrics=self.metrics,
        )

    @patch("enrichment.provider.urllib.request.urlopen")
    def test_sends_auth_and_required_v2_header(self, urlopen):
        urlopen.return_value = response(
            {"status": "ok", "results": [{"domain": "a.com", "status": "ok"}]}
        )

        results = self.make_client().enrich(["a.com"])

        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(request.get_header("X-provider-version"), "2")
        self.assertEqual(json.loads(request.data), {"domains": ["a.com"]})
        self.assertEqual(results[0]["domain"], "a.com")
        self.assertEqual(self.metrics.requests, 1)

    @patch("enrichment.provider.sleep_before_retry")
    @patch("enrichment.provider.urllib.request.urlopen")
    def test_honors_retry_after_on_429(self, urlopen, sleep):
        headers = Message()
        headers["Retry-After"] = "1"
        rate_limit = urllib.error.HTTPError(
            "http://provider.test",
            429,
            "Too Many Requests",
            headers,
            response({"status": "error", "code": "RATE_LIMITED"}),
        )
        urlopen.side_effect = [
            rate_limit,
            response({"status": "ok", "results": []}),
        ]

        results = self.make_client().enrich(["a.com"])

        self.assertEqual(results, [])
        sleep.assert_called_once_with(1, 1.0)
        self.assertEqual(self.metrics.requests, 2)
        self.assertEqual(self.metrics.request_retries, 1)

    @patch("enrichment.provider.urllib.request.urlopen")
    def test_malformed_json_becomes_bounded_transport_failure(self, urlopen):
        urlopen.return_value = io.BytesIO(b"not-json")

        with self.assertRaises(ProviderError) as raised:
            self.make_client(max_attempts=1).enrich(["a.com"])

        self.assertEqual(raised.exception.code, "TRANSPORT")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(self.metrics.requests, 1)
