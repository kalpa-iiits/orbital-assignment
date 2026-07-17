# Decisions

## What I optimized for

My first priority was reconciliation: given an input file, an operator must be
able to account for every row after the run. My second priority was predictable
behavior against an opaque, rate-limited vendor. I favored bounded resource use
and explicit failures over maximum local throughput.

## Calls I made

### Use the batch endpoint, but stay conservative

I use sequential batches of 10. The vendor permits 25, but each domain still
consumes rate-limit capacity, so larger or concurrent batches do not create free
throughput. Ten reduces HTTP overhead while leaving headroom for an opaque/shared
limit. I made the size configurable because the right production value should be
based on measured quota and latency, not hard-coded from one mock run.

I considered bounded concurrency, but did not add it. With a rate-limited vendor,
concurrency without a calibrated client-side limiter mostly converts useful work
into `429` responses. Sequential batching is simpler and can still consume the
documented domain-level rate over a long run.

### Treat request failures and item failures separately

A batch request can fail as a whole because of rate limiting, transport errors,
or a timeout. It can also return HTTP 200 while individual domains fail. The code
therefore has two bounded retry paths:

- request-wide transient failures honor `Retry-After` or use capped exponential
  full jitter;
- retryable item failures are retried without resending items that already
  succeeded.

Both paths stop after the configured attempt limit. Once retries are exhausted,
the affected domains are written as failures instead of hanging the run or being
dropped. I chose a six-second request timeout because the documented slow calls
can take several seconds; a shorter value would manufacture avoidable retries.

### Preserve one outcome per CSV row

I preserve input order and cardinality. Invalid domains, blank records,
duplicates, case variants, no-matches, and exhausted transient failures all
produce output records. Each record includes the source CSV line number and
original value, which makes the result reconcilable without relying on domain
uniqueness.

I deliberately did not globally deduplicate domains. A global cache adds memory
or persistent-state requirements and complicates row-level accounting. If vendor
cost made deduplication important, I would add a durable domain-result cache while
still emitting one result per source row.

### Normalize useful fields without discarding evidence

The provider returns legitimate variations rather than one stable schema. I map
employee counts to `exact`, `range`, or `unknown`; industries to a list; and
locations to consistent city/country keys. I do not turn a band such as
`1,000-5,000` into a guessed point value.

The normalized view is convenient for downstream consumers, but normalization
rules can be incomplete or change. I therefore retain the untouched provider
record beside it. That allows auditing or reprocessing without purchasing the
same enrichment again.

### Validate the protocol, not just HTTP status

Every request pins provider version 2. I check top-level status, result count,
each item's status, the nested `provider_version`, and both returned domain
fields before accepting a success. This is intentional because HTTP 200 does not
guarantee per-domain success. A version, shape, domain, or count mismatch becomes
`BAD_RESPONSE`; I do not guess which result belongs to which input.

### Stream and publish atomically

CSV input and JSONL output are streamed in bounded groups, so memory use does not
grow with a 100k-row file. JSONL also allows downstream streaming and naturally
represents mixed success/failure outcomes.

The final result and optional summary are written through temporary sibling files
and atomically replaced only after complete writes. Input, output, and summary
paths must be distinct. A fatal error or interruption therefore cannot present a
partial result as a completed run. The trade-off is no checkpoint/resume: a
crashed run starts over.

## Assumptions

- The input is CSV with a header named `domain`, unless `--column` overrides it.
- Syntax validation is enough before calling the vendor. DNS and public-suffix
  checks could reject legitimate records and introduce another network dependency.
- A completed run with domain-level failures is useful, but must return exit code
  1. Configuration or file errors return 2.
- The batch response is positional, as documented. I still validate count and
  returned domain before attaching a result to an input row.

## Where I stopped

I did not add a framework, database, async runtime, or production observability
stack. They would increase the review surface without improving the core behavior
this exercise evaluates.

With another day, I would add durable checkpoints and idempotent input IDs for
expensive long-running jobs, then introduce a measured token-bucket scheduler
with small bounded concurrency. I would also emit structured progress and latency
metrics, and add socket-level timeout/disconnect tests using a fake HTTP server.
