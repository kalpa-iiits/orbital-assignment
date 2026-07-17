# AI log

Tool used: OpenAI Codex. I used it to inspect the supplied materials, draft the
stdlib Python implementation and review comments, and run unit/integration
checks. I kept the design choices explicit rather than accepting the first
plausible implementation.

Specific corrections and overrides:

1. **Rejected maximum concurrency/batch enthusiasm.** An early direction was to
   maximize the documented 25-item batch and run batches concurrently. That is
   locally fast but ignores the shared rate limit and creates retry storms at
   scale. I chose sequential batches of 10 with configurable size, bounded retry,
   `Retry-After`, and jitter.
2. **Rejected `parseInt`-style employee normalization.** Treating all strings as
   numbers corrupts `"1,000-5,000"` (often into `1`) and erases that it is a
   range. I modeled employee counts as exact/range/unknown and retained the raw
   provider record.
3. **Rejected dropping bad rows.** A convenient draft path filtered invalid
   domains and terminal provider failures. That conflicts with the assignment's
   no-silent-loss requirement. Every CSV row now produces an ordered success or
   failure record, and failures drive a non-zero exit status.
4. **Distrusted HTTP-only success handling.** A generic client pattern called
   every HTTP 200 a success. The API explicitly warns against that, so the code
   checks top-level and per-item body status and treats malformed/count-mismatched
   responses as visible protocol failures.
5. **Overrode a lossy “clean schema only” output.** Normalized fields are useful,
   but provider schemas evolve and normalization can contain bugs. I included the
   untouched `provider_data` beside normalized data so results can be audited and
   reprocessed without paying the vendor again.

One process mistake is worth calling out: Codex's initial broad repository-read
command opened `mock-provider.js` along with the assignment and API docs before it
had parsed the instruction to treat that file as opaque. I corrected course by
not inspecting or modifying it further and validated behavior through the HTTP
interface. This is exactly the kind of overly broad AI/tool action I would prevent
next time by reading the assignment alone first and narrowing subsequent file
targets explicitly.
