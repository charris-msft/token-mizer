---
name: token-mizer-budget
description: Only for the selected Token Mizer agent or an explicit request to activate Token Mizer. Check a private dated policy before paid fallback or budget exceptions.
---

# Paid-route gate

Apply only while Token Mizer is selected or explicitly activated.

Read the private policy from `$COPILOT_HOME/token-mizer/policy.json` when `COPILOT_HOME` is set, otherwise from the host's Copilot configuration directory under `token-mizer/policy.json`. The file is a dated local allocation, not live billing data or enforcement. If it is missing, invalid, or expired, remain on Foundry and ask before paid fallback. Never initialize an allowance silently.

## Before authorizing paid fallback

1. Validate `validFrom`, `validThrough`, `timeZone`, weekday/weekend rules, per-task allowance, and approval-only reserve.
2. Confirm the exact model and billing provider using authoritative host data.
3. Read authoritative account-wide spend and task-attributed costs with timestamps. Tokens, API calls, and generic cost multipliers are not dollars.
4. Account for concurrent work, pending charges, and prior reserve use. A logical task shares one allowance across its coordinator, workers, retries, and reviews.
5. Require a supported way to keep in-flight spending within the remaining allowance. This instruction-only plugin does not provide metering, atomic reservations, or a technical cap.
6. If any prerequisite is missing, block automatic paid use. Offer continued Foundry work or request a specific exception naming the task, model, reason, requested allowance, and missing enforcement.

Five minutes of Foundry rate limiting makes fallback eligible, not approved. Never fabricate spend, silently use reserve, or approve an increase.

## Report

State the recommended route, approved allowance, measured spend and timestamp or `unknown`, pending commitments or `unknown`, calculable headroom, and next decision. Keep private values local unless the user explicitly requests sharing.
