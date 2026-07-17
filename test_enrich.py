import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import enrich


class FakeClient:
    max_attempts = 3

    def __init__(self, responses):
        self.responses = iter(responses)

    def enrich(self, domains):
        return next(self.responses)


class EnrichTests(unittest.TestCase):
    def test_normalizes_messy_fields_without_losing_raw_data(self):
        data = {
            "name": "Acme",
            "employeeCount": "1,000-5,000",
            "industry": "SaaS",
            "location": "Austin",
        }
        normalized = enrich.normalize_data(data)
        self.assertEqual(normalized["employee_count"], {"kind": "range", "min": 1000, "max": 5000})
        self.assertEqual(normalized["industries"], ["SaaS"])
        self.assertEqual(normalized["location"], {"city": "Austin", "country": None})

    @patch("enrich.ProviderClient._sleep")
    def test_retries_only_retryable_items(self, _sleep):
        rows = [enrich.InputRow(2, "a.com", "a.com"), enrich.InputRow(3, "b.com", "b.com")]
        client = FakeClient([
            [
                {"domain": "a.com", "status": "error", "code": "TEMPORARY", "retryable": True},
                {"domain": "b.com", "status": "error", "code": "NO_MATCH"},
            ],
            [{"domain": "a.com", "status": "ok", "data": {"name": "A"}}],
        ])
        metrics = enrich.Metrics()
        records = enrich.enrich_batch(rows, client, metrics)
        self.assertEqual([record["status"] for record in records], ["succeeded", "failed"])
        self.assertEqual(records[1]["error"]["code"], "NO_MATCH")
        self.assertEqual(metrics.item_retries, 1)

    def test_does_not_attach_a_result_to_the_wrong_domain(self):
        rows = [enrich.InputRow(2, "a.com", "a.com")]
        client = FakeClient(
            [[{"domain": "other.com", "status": "ok", "data": {"name": "Other"}}]]
        )
        records = enrich.enrich_batch(rows, client, enrich.Metrics())
        self.assertEqual(records[0]["status"], "failed")
        self.assertEqual(records[0]["error"]["code"], "BAD_RESPONSE")

    def test_invalid_rows_are_visible_and_do_not_call_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "in.csv"
            output = Path(directory) / "out.jsonl"
            source.write_text("domain\nnot a domain\n", encoding="utf-8")
            args = Namespace(
                input=str(source), output=str(output), summary=None, column="domain",
                base_url="http://unused", token="token", batch_size=10, timeout=1,
                max_attempts=1,
            )
            summary = enrich.run(args)
            record = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(summary["failure_reasons"], {"INVALID_DOMAIN": 1})
            self.assertEqual(record["status"], "failed")
            self.assertEqual(summary["provider_requests"], 0)


if __name__ == "__main__":
    unittest.main()
