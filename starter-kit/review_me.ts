// review_me.ts
//
// PART B — Code review.
//
// A teammate generated the function below with an AI assistant and opened a PR.
// It "works" against the mock provider for a handful of domains and they'd like
// to merge it. Review it as you would a real PR: leave comments inline (a `//
// REVIEW:` line above the relevant code is fine) covering correctness, scale,
// data quality, failure handling, and anything else you'd block or push back on.
//
// You do NOT need to rewrite it. We care about what you catch and how you
// prioritize it. Do not run it against the provider before reading it — read
// first, the way you would in a real review.

type Company = {
  domain: string;
  name: string;
  employees: number;
  industry: string | string[];
};

const PROVIDER_URL = "http://localhost:4000";
// REVIEW [BLOCKER/security]: Credentials must come from secret-backed runtime
// configuration. A committed token is both a leak and difficult to rotate.
const PROVIDER_TOKEN = "demo-token-abc123";

export async function enrichDomains(domains: string[]): Promise<Company[]> {
  // REVIEW [BLOCKER/security]: This prints the bearer credential to logs. Logs
  // commonly have broad access and long retention; log only safe request metadata.
  console.log(`Enriching ${domains.length} domains with token ${PROVIDER_TOKEN}`);

  // REVIEW [BLOCKER/scale]: This starts one unbounded request and retry loop per
  // input. At 100k rows it creates 100k promises, exhausts sockets/memory, and
  // immediately overwhelms the rate limit. Use bounded workers or the batch API
  // with explicit backpressure.
  const results = await Promise.all(
    domains.map(async (domain) => {
      // REVIEW [BLOCKER/reliability]: An infinite retry loop can hang the entire
      // run forever. Bound attempts/elapsed time, add exponential jitter, and
      // surface exhausted failures in the result and run summary.
      while (true) {
        try {
          // REVIEW [BLOCKER/correctness]: Encode the query value (URLSearchParams)
          // and normalize/validate domains first. Raw '&', '#', spaces, or Unicode
          // can change the request or silently enrich the wrong value.
          const res = await fetch(`${PROVIDER_URL}/v1/enrich?domain=${domain}`, {
            // REVIEW [BLOCKER/correctness]: The required X-Provider-Version: 2
            // header is missing. This selects deprecated v1, where fields such as
            // name and employeeCount have different names.
            headers: { Authorization: `Bearer ${PROVIDER_TOKEN}` },
            // REVIEW [BLOCKER/reliability]: There is no timeout/AbortSignal, so a
            // slow or stuck provider call can hold the whole Promise.all forever.
          });

          if (res.status === 429 || res.status >= 500) {
            // REVIEW [BLOCKER/reliability]: This hot-loops immediately and ignores
            // Retry-After. It amplifies an outage/rate limit. Sleep according to
            // Retry-After or capped exponential backoff with jitter.
            continue;
          }

          // REVIEW [BLOCKER/correctness]: HTTP status alone is insufficient here.
          // Check body.status/code (NO_MATCH can arrive with HTTP 200), handle
          // 401/other 4xx explicitly, and guard JSON parsing/content shape.
          const body: any = await res.json();
          const data = body.data;

          return {
            domain: data.domain,
            name: data.name,
            // REVIEW [BLOCKER/data quality]: employeeCount can be null, a numeric
            // string, or a band such as "1,000-5,000". parseInt turns the latter
            // into 1 and null into NaN. Preserve exact/range/unknown semantics.
            employees: parseInt(data.employeeCount),
            industry: data.industry,
          };
        } catch (e) {
          // REVIEW [BLOCKER/operations]: Returning null silently loses input and
          // conflates timeout, network, parse, and programming errors. Return a
          // per-domain failure record with code/attempts and include it in a run
          // summary; retry only failures known to be transient.
          return null;
        }
      }
    })
  );

  // REVIEW [BLOCKER/data loss]: Filtering failures violates one-output-per-input
  // traceability. It also makes duplicates and missing results impossible to
  // reconcile. The public return type should model success and failure outcomes.
  return results.filter(Boolean) as Company[];
}
