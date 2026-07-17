# PR walkthrough

## Problem framing

The main invariant I designed around is that every CSV input record must have an
observable outcome. A no-match, invalid domain, timeout, or exhausted transient
failure is still a result; it must not disappear from the output.

I also treated the sample size as misleading. The supplied file is small, but
the implementation should behave predictably with 100k rows, so it cannot load
the entire input, create one task per domain, or retry forever.

## Execution flow

`enrich.py` is intentionally only an entry point. It calls `enrichment/cli.py`,
which parses options, validates that file paths cannot overwrite one another,
and creates `PipelineConfig`.

`EnrichmentPipeline` in `enrichment/pipeline.py` then performs the run:

1. It streams CSV records and preserves blank physical rows.
2. It normalizes and validates domains locally.
3. It groups valid domains into bounded batches of 10.
4. It sends each batch through `ProviderClient`.
5. It retries only failures that are safe to retry.
6. It writes one ordered JSONL record for every input record.
7. It atomically publishes the output and returns an operator-facing summary.

The HTTP-specific behavior lives in `enrichment/provider.py`. Every call includes
the bearer token and `X-Provider-Version: 2`. Request-wide transient failures use
`Retry-After` when available, otherwise capped exponential full jitter. Requests
also have a six-second timeout and a fixed attempt limit.

The batch response has a second level of failure handling. A request can succeed
while individual domains return `TEMPORARY` or `NO_MATCH`. The pipeline retains
successful items, retries only retryable items, and writes terminal failures with
their provider code. It also checks result count, nested provider version, and
both returned domain fields before attaching a response to an input row.

## Data handling

Provider values are inconsistent but not necessarily wrong. The normalization
module converts them into a predictable downstream shape:

- employee count becomes `exact`, `range`, or `unknown`;
- industry is always a list;
- location always contains city and country keys;
- optional values such as founded year remain nullable.

I keep `provider_data` next to this normalized view. This is deliberate: if a
normalization rule changes, the data can be reprocessed without calling and
paying the vendor again.

Every output line includes the source line number, original domain value, and
normalized domain. Duplicates and case variants remain separate output records,
which makes reconciliation straightforward. Completely blank row 40 is retained
as an `INVALID_DOMAIN` failure rather than silently skipped.

## Scale and operational behavior

Memory use is bounded by the batch size because both input and output are
streamed. I chose sequential batches rather than concurrent workers because each
domain consumes vendor quota; uncontrolled concurrency would increase rate-limit
failures without increasing sustainable throughput.

The final JSONL and optional summary are written through temporary sibling files
and atomically replaced. Fatal errors and interruptions therefore do not leave a
partial file at the requested output path. A completed run containing domain
failures returns exit code 1, while configuration and file errors return 2.

The printed summary includes total, succeeded, failed, failure reasons, provider
request count, and both request/item retry counts. This gives an operator enough
information to distinguish bad input, no-match data, and vendor reliability
problems.

## Verification and deliberate limits

The 10 tests cover normalization, selective item retry, wrong-domain responses,
blank input records, required auth/version headers, `429` handling, malformed
JSON, atomic summary publication, and path safety. I also ran the full CLI against
the supplied local provider and confirmed that all 40 input records appeared in
the output.

I intentionally did not add a framework, database, async runtime, or generalized
job system. The next production step would be durable checkpoint/resume and
idempotent input IDs, followed by a measured token-bucket scheduler if sequential
throughput proved insufficient.
