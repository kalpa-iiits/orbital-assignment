# Decisions

- **Stream with bounded sequential batches.** CSV and JSONL are processed in
  groups of 10, keeping memory bounded and reducing round trips without flooding
  the vendor. Ten stays below the documented maximum and leaves rate-limit
  headroom. `429` honors `Retry-After`; other transient request failures use
  capped exponential full jitter. Throughput is intentionally conservative and
  the batch size is configurable.
- **Bound every retry.** Request-level transport/HTTP failures and retryable
  per-item outcomes get at most four attempts. Only failed items are retried, so
  successful domains do not consume quota twice. An exhausted item becomes an
  explicit output failure.
- **Trust the body as well as HTTP.** Batch items are checked independently and
  protocol anomalies (wrong result count/shape) fail visibly. Every request pins
  provider v2.
- **Normalize without destroying evidence.** Domains are trimmed, lowercased,
  IDNA-encoded, and syntax-checked locally. Employee counts become
  exact/range/unknown, industries always become lists, and locations get stable
  city/country keys. The untouched provider record is also retained for audit or
  future reprocessing.
- **Preserve input cardinality and order.** Invalid inputs, no-matches, duplicates,
  and case variants each get an output row. I chose traceability over global
  deduplication; a large-input cache would otherwise add memory/state and unclear
  semantics around duplicate source rows.
- **Publish atomically.** Results go to a temporary sibling and replace the final
  JSONL only after the input finishes. This avoids presenting truncation as a
  completed run. The trade-off is that crash recovery requires rerunning the file.

## Assumptions

- A CSV header named `domain` is required unless `--column` overrides it.
- A syntactically invalid domain should not consume vendor quota.
- A partial run is useful, but must return exit code 1 and retain every failure.
- Provider results correspond positionally to batch inputs; a count mismatch is
  treated as a protocol failure rather than guessed at.

## Provider observations

- Success/error cannot be inferred from HTTP status alone; per-item body status
  is authoritative.
- Fields vary in type, including employee bands and string/object locations.
- Slow responses, transient items, and request-wide rate limits require separate
  handling.

## Known limitations / another day

- There is no checkpoint/resume. For very costly 100k+ runs I would write to a
  durable store with idempotent input IDs and resumable partitions.
- Sequential batching favors predictability over maximum throughput. I would add
  an adaptive token-bucket scheduler with small bounded concurrency, driven by
  measured vendor limits and metrics.
- The CLI has logs only at completion. Production would emit structured progress,
  latency/retry metrics, and redact provider messages before wider distribution.
- Domain validation is intentionally syntactic; it does not perform DNS or public
  suffix validation. Those checks could reject legitimate internal/vendor data.
- Tests cover normalization, selective retry, and visible invalid rows. More time
  would add a fake HTTP server for timeout, 429, malformed response, and atomic
  publication integration tests.
