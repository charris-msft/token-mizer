---
name: token-mizer-budget
description: Only for the selected Token Mizer agent or an explicit request to activate Token Mizer. Check a private dated policy before proactive Gemini builds, paid fallback, or budget exceptions.
---

# Paid-route gate

Apply only while Token Mizer is selected or explicitly activated.

Read the private policy from `$COPILOT_HOME/token-mizer/policy.json` when `COPILOT_HOME` is set, otherwise from the host's Copilot configuration directory under `token-mizer/policy.json`. The file is a dated local allocation, not live billing data or enforcement. If it is missing, invalid, expired, zero-valued, or cannot be enforced, remain on Foundry. Never initialize an allowance silently. An optional `github_models.bounded_builder_model` may name the preferred paid builder; when absent, the explicit Token Mizer preference is `gemini-3.8-flash` if the host exposes it.

## Before authorizing any paid route

1. Read the `github_budget` object and validate its snake_case fields: `valid_from`, `valid_through`, `time_zone`, `weekday_allowance`, `weekend_automatic_allowance`, `task_allowance`, `reserve_requires_explicit_approval`, and `user_reported_remaining_approximate`.
2. Confirm the exact model and billing provider using authoritative host data.
3. Read authoritative account-wide spend and task-attributed costs with timestamps. Tokens, API calls, and generic cost multipliers are not dollars.
4. Account for concurrent work, pending charges, and prior reserve use. A logical task shares one allowance across its coordinator, workers, retries, and reviews.
5. Require a supported way to keep in-flight spending within the remaining allowance. This instruction-only plugin does not provide metering, atomic reservations, or a technical cap.
6. If any prerequisite is missing, block automatic paid use. Offer continued Foundry work or request a specific exception naming the task, model, reason, requested allowance, and missing enforcement.

For substantial bounded implementation or build work, an otherwise valid policy may authorize proactive `gemini-3.8-flash` use immediately; it does not need five minutes of prior Foundry throttling. Confirm the accepted runtime ID, task allowance, authoritative metering, pending commitments, and bounded in-flight enforcement for that one assignment. Preserve a provider-qualified ID supplied by the host, but treat the bare ID as GitHub-billed unless authoritative metadata proves otherwise.

Separately, five minutes of evidenced Foundry rate limiting makes capacity fallback eligible, not approved. Authentication, environment, and provider-configuration failures never satisfy this condition. Gemini preference must not bypass that threshold for a separate capacity incident. Never fabricate spend, silently use reserve, approve an increase, or imply that user preference overrides a zero-budget period.

## Report

State the recommended route, approved allowance, measured spend and timestamp or `unknown`, pending commitments or `unknown`, calculable headroom, and next decision. Keep private values local unless the user explicitly requests sharing.
