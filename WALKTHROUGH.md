# PR walkthrough

This change adds a small, dependency-free enrichment job designed around one
invariant: every input row has an observable outcome.

`enrichment/pipeline.py` contains `EnrichmentPipeline`, which streams CSV into
bounded batches and owns run orchestration, output, and metrics. It uses the pure
helpers in `enrichment/normalization.py` before calling the v2 batch endpoint.
`ProviderClient` in `enrichment/provider.py` separately distinguishes request-wide
failures (HTTP, transport, invalid top-level responses) from item failures.
Request-wide transient failures use bounded backoff; per-item `TEMPORARY`
outcomes retry only those domains. `NO_MATCH` and other terminal outcomes are
written immediately.

Successful vendor data is mapped into a stable schema without pretending messy
values are more precise than they are. Employee counts explicitly distinguish
exact values, ranges, and unknowns; industries are lists; locations always expose
city/country. Raw provider data remains next to the normalized view as an audit
trail.

Output is JSON Lines because a 100k-row job should not build one large in-memory
JSON document. It is written to a sibling temporary file and atomically published
at completion. Each line carries the CSV row number and original value, so even
duplicates and case variants reconcile with the source. The printed/persisted
summary reports success, failure reason counts, provider requests, and retries;
partial failure returns exit code 1.

The intentionally conservative choice is sequential batches of 10. It is safe
under an opaque shared vendor limit and still uses the batch endpoint. If measured
throughput later matters, the extension point is an adaptive rate limiter with a
small concurrency bound—not unbounded `Promise.all`-style fan-out.

The tests lock down the highest-risk behavior: non-lossy messy-field normalization,
selective per-item retries, required v2/auth headers, `429` handling, malformed
responses, safe output paths, and visible invalid inputs that do not waste quota.
`starter-kit/review_me.ts` contains inline blocker comments covering
credentials, v2 versioning, unbounded concurrency/retries, timeouts/backoff,
response semantics, data corruption, and silent loss.
