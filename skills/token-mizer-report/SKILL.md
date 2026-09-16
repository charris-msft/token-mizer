---
name: token-mizer-report
description: Analyze Token Mizer session evidence to report routing effectiveness, latency, failures, and model usage without exporting private content.
---

# Token Mizer effectiveness report

Run only when Token Mizer is selected or the user explicitly requests a Token Mizer routing report.

## Evidence

Prefer the host's structured session history. Time-bound every query and select only the fields needed. Identify Token Mizer sessions through agent metadata when available; otherwise require the user to identify the sessions rather than scanning unrelated conversation text.

Use available session, usage, and tool-execution records to measure:

- sessions and tasks by model family and provider class;
- completion and failure proxies, including provider authentication, configuration, throttling, tool, test, and reasoning failures;
- median and p90 duration where timestamps are reliable;
- input, output, and cache tokens by model when available;
- retry counts, duplicate-worker signals, and bounded-retry compliance;
- paid-route eligibility, approval, and observed use as separate states;
- first-fix success and post-failure Astra escalation when evidence supports them.

Never treat a billing multiplier as currency. Never infer success solely from a final assistant message, or claim that routing caused an outcome from a small or uncontrolled sample.

## Privacy

Do not collect or export prompts, source code, credentials, tokens, private budget values, provider connection IDs, personal paths, or user identities. Aggregate before display. Suppress examples that could reveal sensitive content. This skill reads host-retained evidence in place and creates no hook, external telemetry stream, or background collector.

## Report

Lead with a verdict and concrete baseline. Include:

1. coverage window and number of sessions/tasks;
2. route distribution across Sol, Luna, Astra, and explicitly approved GitHub fallback;
3. outcome, latency, token, retry, and failure-class metrics;
4. policy violations or missing evidence;
5. the highest-impact routing change, expected effect, and confidence;
6. limitations and the minimum additional evidence needed.

Use a dashboard widget when the host provides a suitable one; otherwise return a compact Markdown table. For recurring reports, use only a host-supported automation explicitly requested by the user. Do not claim a scheduled report exists until the scheduler confirms it.

If structured session history or model attribution is unavailable, report the missing capability and stop. Do not fabricate route markers or reconstruct private prompts.
