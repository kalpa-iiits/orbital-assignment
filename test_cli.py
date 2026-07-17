import tempfile
import unittest
from pathlib import Path

from enrichment.cli import validate_paths, write_atomic
from enrichment.models import PipelineConfig


def config(input_path, output_path):
    return PipelineConfig(
        input_path=input_path,
        output_path=output_path,
        column="domain",
        base_url="http://unused",
        token="token",
        batch_size=10,
        timeout=1,
        max_attempts=1,
    )


class CliTests(unittest.TestCase):
    def test_rejects_output_that_would_overwrite_input(self):
        path = Path("same.csv")
        with self.assertRaisesRegex(ValueError, "--output"):
            validate_paths(config(path, path), None)

    def test_rejects_summary_that_would_overwrite_results(self):
        output = Path("output.jsonl")
        with self.assertRaisesRegex(ValueError, "--summary"):
            validate_paths(config(Path("input.csv"), output), output)

    def test_writes_summary_and_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "summary.json"
            write_atomic(path, "{}\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "{}\n")
