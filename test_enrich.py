import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from enrichment import EnrichmentPipeline, InputRow, PipelineConfig, normalize_data


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)

    def enrich(self, domains):
        return next(self.responses)


def pipeline_config(**overrides):
    values = {
        "input_path": Path("unused.csv"),
        "output_path": Path("unused.jsonl"),
        "column": "domain",
        "base_url": "http://unused",
        "token": "token",
        "batch_size": 10,
        "timeout": 1,
        "max_attempts": 3,
    }
    values.update(overrides)
    return PipelineConfig(**values)


class EnrichTests(unittest.TestCase):
    def test_normalizes_messy_fields_without_losing_raw_data(self):
        data = {
            "name": "Acme",
            "employeeCount": "1,000-5,000",
            "industry": "SaaS",
            "location": "Austin",
        }
        normalized = normalize_data(data)
        self.assertEqual(normalized["employee_count"], {"kind": "range", "min": 1000, "max": 5000})
        self.assertEqual(normalized["industries"], ["SaaS"])
        self.assertEqual(normalized["location"], {"city": "Austin", "country": None})

    @patch("enrichment.pipeline.sleep_before_retry")
    def test_retries_only_retryable_items(self, _sleep):
        rows = [InputRow(2, "a.com", "a.com"), InputRow(3, "b.com", "b.com")]
        client = FakeClient([
            [
                {"domain": "a.com", "status": "error", "code": "TEMPORARY", "retryable": True},
                {"domain": "b.com", "status": "error", "code": "NO_MATCH"},
            ],
            [{
                "domain": "a.com",
                "status": "ok",
                "data": {"domain": "a.com", "provider_version": 2, "name": "A"},
            }],
        ])
        pipeline = EnrichmentPipeline(pipeline_config(), client)
        records = pipeline.enrich_batch(rows)
        self.assertEqual([record["status"] for record in records], ["succeeded", "failed"])
        self.assertEqual(records[1]["error"]["code"], "NO_MATCH")
        self.assertEqual(pipeline.metrics.item_retries, 1)

    def test_does_not_attach_a_result_to_the_wrong_domain(self):
        rows = [InputRow(2, "a.com", "a.com")]
        client = FakeClient(
            [[{"domain": "other.com", "status": "ok", "data": {"name": "Other"}}]]
        )
        records = EnrichmentPipeline(pipeline_config(), client).enrich_batch(rows)
        self.assertEqual(records[0]["status"], "failed")
        self.assertEqual(records[0]["error"]["code"], "BAD_RESPONSE")

    def test_rejects_success_data_from_wrong_provider_version(self):
        rows = [InputRow(2, "a.com", "a.com")]
        client = FakeClient([[
            {
                "domain": "a.com",
                "status": "ok",
                "data": {"domain": "a.com", "provider_version": 1},
            }
        ]])

        records = EnrichmentPipeline(pipeline_config(), client).enrich_batch(rows)

        self.assertEqual(records[0]["status"], "failed")
        self.assertEqual(records[0]["error"]["code"], "BAD_RESPONSE")
        self.assertIn("version", records[0]["error"]["message"])

    def test_invalid_and_empty_rows_are_visible_and_do_not_call_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "in.csv"
            output = Path(directory) / "out.jsonl"
            source.write_text("domain\n\nnot a domain\n", encoding="utf-8")
            config = pipeline_config(
                input_path=source,
                output_path=output,
                max_attempts=1,
            )
            summary = EnrichmentPipeline(config).run()
            records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(summary["failure_reasons"], {"INVALID_DOMAIN": 2})
            self.assertEqual([record["row_number"] for record in records], [2, 3])
            self.assertEqual(records[0]["input_domain"], "")
            self.assertTrue(all(record["status"] == "failed" for record in records))
            self.assertEqual(summary["provider_requests"], 0)


if __name__ == "__main__":
    unittest.main()
