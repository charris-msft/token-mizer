---
name: token-mizer-report
description: Automatically use for token rates, TPM, tokens per second, model speed or usage comparisons, provider comparisons, and Token Mizer effectiveness reports.
---

# Token Mizer usage and throughput report

Run only when Token Mizer is selected or the user explicitly requests this skill. Reporting does not activate Token Mizer routing globally.

## Fast path

Start with structured usage records, not conversation summaries, raw assistant text, or recursive history searches. Do not delegate a simple report and do not query Azure unless the user explicitly asks for configured deployment quota.

1. Prefer `session_store_sql` with `source: local` for one bounded aggregate query when provider attribution is not requested.
2. Otherwise run the bundled dependency-free helper `scripts/token_mizer_report.py`. It opens `session-store.db` in SQLite read-only URI mode, introspects the schema, uses parameterized SQL, applies exact UTC cutoffs in Python, and reports honest `UNAVAILABLE` errors for missing data.
3. Resolve the Copilot home from `COPILOT_HOME`, otherwise `~/.copilot`. Always require explicit inclusive start and exclusive end cutoffs.

The known local source is `assistant_usage_events`. Required fields are `session_id`, `agent_id`, `model`, `output_tokens`, `duration_ms`, `api_endpoint`, and `created_at`. Timing, reasoning, input, cache, and cost-ledger fields are optional; report their coverage and keep missing values unknown. The productive main-agent API cohort requires:

- `agent_id IS NULL`;
- `api_endpoint IS NOT NULL` because null endpoints can be aggregate records;
- `output_tokens > 0` and `duration_ms > 0`;
- an explicit model pattern and time window.

Example helper command:

```powershell
python scripts\token_mizer_report.py --start 2026-09-01T00:00:00Z --end 2026-09-08T00:00:00Z --model-like "%opus%"
```

Example bounded SQL shape for `session_store_sql`:

```sql
SELECT model,
       COUNT(*) AS calls,
       COUNT(DISTINCT session_id) AS sessions,
       AVG(60000.0 * output_tokens / duration_ms) AS mean_per_call_output_tpm,
       60000.0 * SUM(output_tokens) / SUM(duration_ms) AS pooled_overall_output_tpm
FROM assistant_usage_events
WHERE substr(created_at, 1, 10) BETWEEN '2026-08-31' AND '2026-09-09'
  AND julianday(created_at) >= julianday('2026-09-01T00:00:00Z')
  AND julianday(created_at) < julianday('2026-09-08T00:00:00Z')
  AND model LIKE '%opus%'
  AND agent_id IS NULL
  AND api_endpoint IS NOT NULL
  AND output_tokens > 0
  AND duration_ms > 0
GROUP BY model
ORDER BY model
LIMIT 100;
```

Use the helper when exact timestamp cutoffs, normalized mixed SQLite/ISO timestamps, exclusion counts, JSON output, or provider attribution matter.

## Metrics

Label these as different averages:

- **Mean per-call output TPM:** `AVG(60000 * output_tokens / duration_ms)` gives every qualifying request equal weight.
- **Pooled overall output TPM:** `60000 * SUM(output_tokens) / SUM(duration_ms)` weights requests by their recorded duration.
- **Output tokens/second:** divide TPM by 60; keep mean and pooled variants separate.
- **Request duration:** mean, median, and nearest-rank p90 of recorded request duration. This is neither pure streaming decode time nor task completion time.
- **Output size:** mean output tokens and fixed output-length strata. Compare similar strata, reasoning settings, dates, and workloads instead of attributing an uncontrolled difference to the model.
- **Coverage:** calls, sessions, calls per session, observed bounds, exclusions, and optional timing/reasoning field coverage.

Keep three different measurements separate:

1. **Per-call output rates:** the mean and pooled output TPM above, measured over recorded request durations.
2. **Deployment wall-clock TPM:** aggregate input plus output tokens across all concurrent requests in each real one-minute interval. When explicitly requested and already authorized, Azure Monitor `TokenTransaction`, `ProcessedPromptTokens`, and `GeneratedTokens` with interval `PT1M` and a `ModelDeploymentName` filter can measure aggregate peaks without an LLM call.
3. **Quota-enforcement estimates:** remaining-token or rate-limit headers and service enforcement state. Estimates can include output caps and differ from recorded or billed token counts.

These are not interchangeable. Monitor aggregates do not prove the exact service-side throttle calculation. Discover models from the bounded records rather than maintaining a hardcoded model list. Report calls, distinct sessions, exact window, exclusions, and source. For recent-versus-historical comparisons, run separately versioned windows with the same filters and disclose non-overlap. Reasoning effort, request size, dates, workload, and concurrency can differ, so do not claim controlled provider causality.

Never calculate from assistant response length, sum cumulative session counters as if they were per-call records, infer currency from multipliers, count missing duration as zero, or mix main-agent requests with subagent or aggregate rollups.

## Recorded cost accounting

Keep the cost cohort separate from the positive-output throughput cohort. Include endpoint-present zero-output leaf calls, but never add endpoint-null aggregate records to leaf calls. `NULL total_nano_aiu` is unknown, not zero: all-unknown totals stay `null`, and partial coverage is an explicitly labeled observed subtotal. Do not multiply recorded cost by `request_multiplier`.

Treat `1,000,000,000` nano-AIU as one recorded AI credit. GitHub documents one AI credit as a `$0.01 USD` equivalent. Label that value as a documented equivalent, not an invoice, charge, Foundry price, or budget-policy balance. `input_tokens` already includes cache-read and cache-write tokens; `output_tokens` already includes billed output represented by the record. Do not add cache or reasoning fields again.

When `token_details_json` supplies token counts, batch sizes, and nano-AIU cost-per-batch metadata, reconcile its exact rational sum against `total_nano_aiu`. Report absent, malformed, incomplete, partial, unpriced, reconciled, and mismatched coverage. Any invalid sibling entry prevents a fully reconciled label. A missing or malformed ledger never becomes a guessed cost.

Synthetic example:

```text
Model            Calls  Sessions  Mean per-call output TPM  Pooled overall output TPM
synthetic-opus       12         4                    3600.0                     4100.0
```

## Optional task ledger

Use `--task-ledger examples\task-ledger.example.json --format json` for annotation-driven task accounting. The versioned JSON supplies explicit task IDs, labels/types, UTC task windows, outcomes, evidence references, scope-completeness flags, per-task-type `acceptance_boundaries`, optional `observed_milestones`, and one or more session/time ownership scopes. Outcomes distinguish CI-passed, merged, deployed, deployed-and-live-verified, failed, blocked, and unfinished. Milestones do not imply one another: count the selected boundary only when it is the explicit outcome or is explicitly listed in `observed_milestones`. Otherwise acceptance is unknown while applicable attempt costs remain included. Do not infer task boundaries from session lifetime or automatically certify evidence references.

Require every ownership scope to fit inside its annotated task window. Reject overlaps both across tasks and within one task/session. Include coordinator and worker leaf calls within owned scopes, including zero-output calls, but exclude endpoint-null aggregates. Keep mixed-model work as a per-model team breakdown. Include failed, blocked, and unfinished applicable attempts in denominators and costs; use `acceptance_applicable: false` only for tasks genuinely outside that type's boundary.

Report these quantities separately:

- annotated task elapsed time from the supplied start/end or cutoff;
- summed inference resource time, which counts parallel workers separately;
- unioned request-active wall time, which merges overlapping request intervals;
- p50 and p90 request duration;
- recorded credits and documented USD equivalent with cost coverage;
- first-pass gate rates only for explicit boolean samples; reopened and rollback rates only for mature records with each corresponding boolean explicitly supplied. Always expose sample counts.

Never infer human idle time, CI waiting, or tool time by subtracting request durations. Cost per accepted task is calculated within a comparable task type only when its acceptance boundary is explicit, at least one applicable task reaches it, every applicable scope is complete, and every owned leaf call has recorded cost. Its numerator includes all applicable attempts, retries, reviews, coordinators, and workers. A deployed-only task is not accepted at a live-verification boundary. Suppress a single global efficiency ratio for heterogeneous task types; do not collapse outcomes into generic success.

Prioritize request-to-verified-live elapsed time when evidence supports it, with active model and tool time reported separately. A `task_complete` event, assistant success claim, or repeated completion marker is not acceptance evidence; keep the explicit outcome and evidence annotation authoritative, and never count duplicate markers as separate tasks. Require an analysis version/cutoff and evidence-coverage statement. Historical comparisons show correlation, not causality: match repository, task class, complexity, settings, context, date, and the identical acceptance boundary, and disclose non-overlapping cohorts. For the bounded-RUG pilot, preserve `pilot_mode` as `baseline` or `bounded-rug`, include every coordinator, builder, reviewer, validator, and failed-attempt scope, and keep real-world pilot results pending until actually observed. Synthetic CLI runs prove mechanics only.

## Conservative provider attribution

Do not infer provider from `api_endpoint`; `/responses` and `ws:/responses` are not provider proof. Current session state cannot identify historical calls after model switches.

Only when provider comparison is requested, run the helper with `--provider-attribution metadata`. For session IDs already selected by the bounded SQL, stream only `session.start` `data.selectedModel` and `session.model_change` `data.newModel` metadata from `session-state/<session_id>/events.jsonl`. Treat usage `created_at` as request completion: compute request start from `duration_ms`, choose the latest selection across all models at request start, then require its model to match the usage row. A selection change during the recorded request, missing history, or malformed relevant metadata makes attribution `unknown`. Resolve a qualified connection ID through read-only `data.db` table `model_providers(id, name, type)` without reading `settings_json` or secrets. A bare selected model with positive Copilot AI-credit evidence is `GitHub billed`; otherwise label it `unknown`. Never inherit a parent selection for subagent rows.

Offer the default unclassified mode when provider comparison is unnecessary or history is missing. Do not recursively scan all session histories. A shared connection does not prove shared quota, and measured throughput must remain separate from configured deployment limits.

## Routing effectiveness

For an effectiveness report, supplement throughput with time-bounded structured session and tool evidence. Keep policy eligibility separate from observed behavior and report:

- tasks and route distribution across Astra coordination, proactive Gemini 3.8 Flash builders, Foundry Luna fallback, explicit-only Sol overrides, and separate capacity fallback;
- context-fit evidence: estimated need, host-reported capacity, unknown/insufficient exclusions, coordinator suitability, and whether a large-context task was blocked rather than sent to Luna;
- completion and failure proxies, separating authentication/environment, throttling/capacity, tool, test, and reasoning failures;
- median and p90 duration when timestamps are reliable;
- input, output, cache, retry, and duplicate-worker evidence when available;
- paid-route eligibility, approval, attempted model, accepted runtime ID, and actual observed use as distinct states;
- proactive Gemini assignments versus five-minute-threshold capacity fallback as separate route reasons;
- first-fix success and post-failure Astra escalation when evidence supports them.

Authentication or unavailable-provider errors require login/environment recovery and are not quota evidence. For explicit 429, TPM, quota, or documented throttling, check bounded retries, `Retry-After`, one active worker per constrained connection, checkpoint reuse, and coordinator yielding. Do not claim Gemini is faster, cheaper, or better from preference or an uncontrolled workload. Do not infer success from the final assistant message, billing from multipliers, or causal routing improvements from small uncontrolled samples. If structured evidence or attribution is unavailable, name the missing capability and stop rather than reconstructing private prompts.

## Privacy and output

Do not collect or export prompts, source code, credentials, tokens, private budget values, provider connection IDs, personal paths, or user identities. Aggregate before display. The helper reads host-retained evidence in place and creates no hook, external telemetry stream, database write, upload, install, or LLM request.

Lead with the answer and concrete baseline. Include the coverage window, calls, sessions, both TPM averages, optional tokens/second, exclusions, source, and limitations. Use a dashboard only when the host provides a suitable local surface. If the table, columns, model attribution, or timestamps are unavailable, state exactly what is missing and stop rather than guessing.
