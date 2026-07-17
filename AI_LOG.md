# AI log

## Tool and workflow

I used OpenAI Codex in the desktop app. I used it to read the assignment and API
documentation, draft the Python implementation, review `review_me.ts`, and run
tests and local provider-backed checks.

I did not treat the first generated version as the submission. My workflow was:

1. read the requirement and provider contract;
2. ask Codex for a dependency-free Python implementation;
3. inspect the failure and data-quality paths;
4. run unit tests and the CLI against the local provider;
5. challenge parts that were difficult to understand or did not preserve every
   input row;
6. refactor and add regression tests for the issues found.

## Places where I corrected or overrode the AI

### 1. I rejected unbounded concurrency

An early direction was to use the largest documented batch size and process
multiple batches concurrently. That looked faster for the sample file, but each
domain consumes rate-limit capacity. At 100k rows it would mainly create `429`
responses and synchronized retries. I chose sequential batches of 10, honored
`Retry-After`, and kept the batch size configurable.

### 2. I rejected lossy employee-count parsing

A simple implementation could parse every employee value as an integer. That
would corrupt a band such as `"1,000-5,000"` and would not distinguish `null`
from malformed data. I changed the normalized schema to represent employee count
as `exact`, `range`, or `unknown`, while retaining the original provider record.

### 3. I caught silent loss of the blank CSV record

The first CSV implementation used `csv.DictReader`. It handled an empty domain
cell, but skipped a completely blank physical record. In the supplied file that
meant row 40 disappeared, which violated the one-outcome-per-input requirement. I
changed the reader to `csv.reader`, preserved blank records as `INVALID_DOMAIN`,
and added a regression test that checks their line numbers and output records.

### 4. I pushed back on a single large source file

The working implementation initially put CLI parsing, HTTP behavior,
normalization, retry policy, and file orchestration in `enrich.py`. It worked, but
I found the workflow unnecessarily difficult to follow. I split it into a small
package: `cli.py`, `pipeline.py`, `provider.py`, `normalization.py`, and
`models.py`. I kept `enrich.py` as a minimal entry point and added a code map to
the README.

### 5. I added protocol checks beyond “HTTP 200 means success”

The provider documentation explicitly warns that HTTP status is not enough. I
made the code check top-level status, each item status, result count, nested v2
version, and returned domains before associating a result with an input row. I
added tests that verify the bearer token, required v2 header,
`429`/`Retry-After`, and malformed JSON behavior.

### 6. I rejected publishing partial or conflicting files

The first version atomically published the JSONL result but wrote the optional
summary directly. It also allowed input, output, and summary paths to collide. I
changed both outputs to use temporary sibling files, clean them up on errors or
interruption, and reject path combinations that could overwrite source data or
results.

## Verification performed

I ran:

```bash
python3 -m unittest -v
python3 -m py_compile enrich.py enrichment/*.py test_*.py
python3 enrich.py --help
```

The final suite has 10 passing tests. In the final local provider-backed run, all
40 input records produced output: 36 succeeded, two were `NO_MATCH`, and two were
invalid inputs, including blank row 40.

## Process mistake

Codex's first broad repository-inspection command opened `mock-provider.js` along
with the assignment and API documentation before the instruction to treat that
file as opaque had been applied. I stopped inspecting or modifying it and used
the documented HTTP interface for subsequent verification. I am recording this
because hiding an AI/tooling mistake would be worse than acknowledging it. Next
time I would read the assignment alone first, then explicitly allow-list the
files used in later inspection commands.
