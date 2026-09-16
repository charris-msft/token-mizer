---
name: token-mizer-route
description: Only for the selected Token Mizer agent or an explicit request to activate Token Mizer. Route substantial work across configured Foundry models, handle rate limits, and escalate difficult decisions.
---

# Foundry-first routing

Apply this skill only while Token Mizer is selected or explicitly activated. Otherwise return without changing routing.

## Routes

Resolve model identifiers from the current host's model catalog. Accept a route only when the host identifies it as the user's configured Foundry provider. Provider connection IDs are account-specific and must remain local.

| Model family | Role |
|---|---|
| Sol | Default worker and routine coordinator |
| Luna | Small, bounded extraction, classification, formatting, or mechanical work with objective checks |
| Astra | Difficult planning, high-risk decisions, and diagnosis after a failed first fix |

No Terra route is configured. Never infer a provider from an unprefixed model name, silently remove a provider prefix, or substitute a GitHub-billed model.

## Execute

1. Keep work requiring five or fewer direct tool calls in the current session.
2. Use Sol for ordinary implementation, research, writing, and coordination.
3. Use Luna only when the work is bounded and acceptance is cheap and objective. If unavailable, use Foundry Sol once rather than retrying or changing provider.
4. Use Astra for genuinely difficult or high-risk decisions and bounded rubber-duck diagnosis after a failed first fix. Give it the reproduction, evidence, and unresolved question.
5. Delegate self-contained work using `token-mizer-handoff`. Prefer one worker and parallelize only independent work with measurable benefit.

## Rate limits and paid fallback

Respect `Retry-After`. After five minutes of actual Foundry rate-limit blocking, a paid fallback becomes eligible for consideration, not authorized. Load `token-mizer-budget` before any paid fallback. If provider identity, authoritative metering, or bounded spending controls are unavailable, remain on Foundry or ask for a specific exception. Avoid retry storms and duplicate work.

## Quality

Reproduce reported bugs through the closest feasible user flow before editing. Verify changed behavior and relevant regression checks. Before merging delegated code, require evidence for the same gates CI runs. Research claims need sources. Report outcomes, verification, and remaining uncertainty concisely.
