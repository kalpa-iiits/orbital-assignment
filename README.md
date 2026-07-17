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

## Code map and workflow

Start reading at `enrich.py`. It only hands control to the CLI. Each module then
has one job:

```text
enrich.py
  -> enrichment/cli.py             parse options and create configuration
  -> enrichment/pipeline.py        read rows, batch work, write output/summary
       -> enrichment/provider.py   HTTP calls, timeouts, request retries
       -> enrichment/normalization.py
                                    validate domains and normalize messy fields
       -> enrichment/models.py     shared configuration, row, and metric objects
```

For each input batch, the workflow is:

1. Read CSV rows without loading the whole file into memory. Blank rows are kept.
2. Normalize and validate each domain. Invalid rows become failure records.
3. Send valid domains to the provider's v2 batch endpoint.
4. Retry request failures and retryable item failures within fixed limits.
5. Normalize successful data and write every success/failure as one JSONL line.
6. Atomically publish the output and print the run summary.

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
