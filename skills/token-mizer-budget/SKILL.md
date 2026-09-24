---
name: token-mizer-budget
description: Verify private local opt-in for the three allowed built-in routes and preserve dated spending gates for other paid routes.
---

# Paid-route gate

Apply only while Token Mizer is selected or explicitly activated through `token-mizer-integrate`. Read `$COPILOT_HOME/token-mizer/policy.json` when set, otherwise the host Copilot configuration directory under `token-mizer/policy.json`. The public example is not approval. Never write a private opt-in, initialize an allowance, or expose local amounts.

## Built-in exception

Only an explicitly user-approved private local policy with `github_models.builtin_uncapped_opt_in` exactly `true` permits *uncapped automatic routing* to GitHub/built-in `gpt-6-sol`, `gpt-6-astra`, and `grok-4.7`. Default, missing, malformed, or false means no built-in automatic use. Verify exact host provider/family/runtime identity, availability, sufficient known context, actor permission, correct role and intended route, and fresh runtime acceptance for **each** assignment. The exception has no invented dollar limit or technical billing cap. It does not extend to Gemini, GPT-5.6 Sol, other built-ins, Foundry IDs, unknown provider billing, or a separate capacity fallback. An instruction-only plugin cannot enforce an account-wide budget or bill.

Supply `paid_policy: {"builtin_uncapped_opt_in": true, "source": "local"}` to the allocator only after that verification. Its booleans and route evidence are caller annotations, not independent proof of private-policy contents or host truth. Recheck before admission; revoke or missing policy blocks reuse. Do not let a stale, previously admitted handoff authorize the next invocation.

## Other paid routes

Keep the existing dated `github_budget` gate for any separately approved paid capacity route. Validate `valid_from`, `valid_through`, `time_zone`, `weekday_allowance`, `weekend_automatic_allowance`, `task_allowance`, `reserve_requires_explicit_approval`, and `user_reported_remaining_approximate`. Confirm authoritative account-wide spend, task-attributed charges, timestamps, concurrency/pending charges, remaining allowance and enforceable in-flight bounds. Missing, expired, zero, or unenforceable data blocks automatic paid use. Five minutes of evidenced Foundry throttling makes a capacity fallback eligible for *evaluation*, not authorization. The allocator has no fresh arbitrary paid-model route. Never turn preference into an implied exception, or mislabel AI credit multipliers as dollars.

Foundry Luna remains an independently eligible economy route when its context fits; it is not a success-shaped fallback when a large-context built-in route is blocked. The DeepSeek pilot requires its separate explicit task approval and verified Foundry identity/capacity; never interpret the built-in exception as Foundry authorization. Report known amounts with timestamps only when requested, and unknowns as unknown.
The user may instead explicitly grant standing approval for **only** bounded deployment-repair DeepSeek pilot tasks. Check the private `foundry_pilots.deepseek_deployment_repair_enabled` flag is exactly `true` and retain a safe reference to the original user's approval; absence/false requires explicit approval for each task. The public example defaults off. Neither a Foundry mapping nor this flag bypasses fresh host/actor/context checks or the durable single-attempt bound; the allocator does not itself read the private policy.
