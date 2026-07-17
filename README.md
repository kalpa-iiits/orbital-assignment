# Enrichment pipeline

A dependency-free Python CLI that reads CSV incrementally, calls provider v2 in
bounded batches, and writes one JSON object per input row. Successes and failures
are both retained, while the terminal summary groups failures into actionable
reason codes.

## Requirements and run commands

- Python 3.10+
- Node 18+ (only for the supplied mock provider)

Terminal 1:

```bash
node starter-kit/mock-provider.js
```

Terminal 2:

```bash
PROVIDER_TOKEN=demo-token python3 enrich.py \
  --input starter-kit/domains.csv \
  --output enriched.jsonl \
  --summary summary.json
```

The summary is always printed to stdout. The optional `--summary` path persists
the same JSON. Exit status is `0` only if every row succeeded, `1` if the run
completed with visible per-row failures, and `2` for a configuration/input error.
This makes partial failure observable in automation without discarding results.

Useful options:

```text
--base-url URL       provider base URL (or PROVIDER_URL)
--batch-size 1..25   bounded batch size (default: 10)
--timeout SECONDS    per-request timeout (default: 6)
--max-attempts N     request and per-item attempt limit (default: 4)
--column NAME        CSV domain column (default: domain)
```

Run the tests:

```bash
python3 -m unittest -v
```

## Output contract

`enriched.jsonl` is replace-on-success: a fatal run error does not publish a
truncated final file. Each line includes the source row number, original input,
normalized domain, and either:

- `status: "succeeded"`, normalized `data`, and untouched `provider_data`; or
- `status: "failed"` and an `error` containing `code`, `message`, and
  `retryable`.

JSON Lines supports streaming consumers and avoids holding a 100k-row result in
memory. Rows retain input order. Duplicate inputs deliberately produce duplicate
output rows so source-to-result reconciliation remains simple.

See [DECISIONS.md](DECISIONS.md) for trade-offs and [WALKTHROUGH.md](WALKTHROUGH.md)
for the PR-style design tour.
