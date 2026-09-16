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

The known local source is `assistant_usage_events`. Required fields are `session_id`, `agent_id`, `model`, `output_tokens`, `duration_ms`, `total_nano_aiu`, `api_endpoint`, and `created_at`. The productive main-agent API cohort requires:

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
WHERE substr(created_at, 1, 10) BETWEEN '2026-09-01' AND '2026-09-08'
  AND model LIKE '%opus%'
  AND agent_id IS NULL
  AND api_endpoint IS NOT NULL
  AND output_tokens > 0
  AND duration_ms > 0
GROUP BY model;
```

Use the helper when exact timestamp cutoffs, normalized mixed SQLite/ISO timestamps, exclusion counts, JSON output, or provider attribution matter.

## Metrics

Label these as different averages:

- **Mean per-call output TPM:** `AVG(60000 * output_tokens / duration_ms)` gives every qualifying request equal weight.
- **Pooled overall output TPM:** `60000 * SUM(output_tokens) / SUM(duration_ms)` weights requests by their recorded duration.
- **Output tokens/second:** divide TPM by 60; keep mean and pooled variants separate.

These describe observed output throughput over recorded request duration. They are not configured TPM quota, total prompt-token processing, or pure streaming decode speed. Report calls, distinct sessions, exact window, exclusions, and source. Reasoning effort, request size, dates, and workload can differ, so do not claim controlled provider causality.

Never calculate from assistant response length, sum cumulative session counters as if they were per-call records, infer currency from multipliers, count missing duration as zero, or mix main-agent requests with subagent or aggregate rollups.

Synthetic example:

```text
Model            Calls  Sessions  Mean per-call output TPM  Pooled overall output TPM
synthetic-opus       12         4                    3600.0                     4100.0
```

## Conservative provider attribution

Do not infer provider from `api_endpoint`; `/responses` and `ws:/responses` are not provider proof. Current session state cannot identify historical calls after model switches.

Only when provider comparison is requested, run the helper with `--provider-attribution metadata`. For session IDs already selected by the bounded SQL, stream only `session.start` `data.selectedModel` and `session.model_change` `data.newModel` metadata from `session-state/<session_id>/events.jsonl`. Match each main-agent usage row to the last preceding selection for that model. Resolve a qualified connection ID through read-only `data.db` table `model_providers(id, name, type)` without reading `settings_json` or secrets. A bare selected model with positive Copilot AI-credit evidence is `GitHub billed`; otherwise label it `unknown`. Never inherit a parent selection for subagent rows.

Offer the default unclassified mode when provider comparison is unnecessary or history is missing. Do not recursively scan all session histories. A shared connection does not prove shared quota, and measured throughput must remain separate from configured deployment limits.

## Privacy and output

Do not collect or export prompts, source code, credentials, tokens, private budget values, provider connection IDs, personal paths, or user identities. Aggregate before display. The helper reads host-retained evidence in place and creates no hook, external telemetry stream, database write, upload, install, or LLM request.

Lead with the answer and concrete baseline. Include the coverage window, calls, sessions, both TPM averages, optional tokens/second, exclusions, source, and limitations. Use a dashboard only when the host provides a suitable local surface. If the table, columns, model attribution, or timestamps are unavailable, state exactly what is missing and stop rather than guessing.
