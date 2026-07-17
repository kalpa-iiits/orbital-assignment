"""Command-line interface for the enrichment pipeline."""

import argparse
import json
import os
import sys
from pathlib import Path

from .models import PipelineConfig
from .pipeline import EnrichmentPipeline


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich domains from CSV and write success/failure JSONL."
    )
    parser.add_argument("--input", default="starter-kit/domains.csv")
    parser.add_argument("--output", default="enriched.jsonl")
    parser.add_argument("--summary", help="also write the run summary as JSON")
    parser.add_argument("--column", default="domain")
    parser.add_argument(
        "--base-url",
        default=os.getenv("PROVIDER_URL", "http://localhost:4000"),
    )
    parser.add_argument("--token", default=os.getenv("PROVIDER_TOKEN"))
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        choices=range(1, 26),
        metavar="1..25",
    )
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
    config = PipelineConfig(
        input_path=Path(args.input),
        output_path=Path(args.output),
        column=args.column,
        base_url=args.base_url,
        token=args.token,
        batch_size=args.batch_size,
        timeout=args.timeout,
        max_attempts=args.max_attempts,
    )

    try:
        summary = EnrichmentPipeline(config).run()
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    rendered = json.dumps(summary, indent=2, sort_keys=True)
    print(rendered)
    if args.summary:
        Path(args.summary).write_text(rendered + "\n", encoding="utf-8")
    return 0 if summary["failed"] == 0 else 1
