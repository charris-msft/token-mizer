---
name: token-mizer-route
description: Only for the selected Token Mizer agent or an explicit request to activate Token Mizer. Route substantial work across verified Foundry models and policy-authorized Gemini builders, handle rate limits, and escalate difficult decisions.
---

# Budget-aware routing

Apply this skill only while Token Mizer is selected or explicitly activated. Otherwise return without changing routing.

## Routes

Read the private local policy described by `token-mizer-budget`. Resolve a Foundry mapping from either of these sources:

1. The current host catalog explicitly identifies the model as Foundry-backed.
2. The user-confirmed local `provider` object supplies `connection_id`, `name`, `default_model`, `escalation_model`, and `bounded_task_model`.

For a local mapping, form runtime IDs as `provider.connection_id/model-name` unless a configured model is already fully qualified. Use an ID only when the runtime tool accepts it. Provider IDs are account-specific and must remain local. If neither source establishes provider identity, fail closed and report that Foundry routing is not configured.

| Model family | Role |
|---|---|
| Astra | Recommended inherited coordinator: planning, decomposition, hard decisions, verification, and diagnosis after a failed first fix |
| Gemini 3.8 Flash | Preferred policy-authorized builder for substantial, bounded implementation or build work |
| Sol | No-paid-budget or unavailable-Gemini fallback for routine work and coordination |
| Luna | Cheap bounded non-build extraction, classification, formatting, or mechanical work with objective checks |

No Terra route is configured. Preserve a provider-qualified ID that the host actually supplies. A bare `gemini-3.8-flash` ID is GitHub-billed unless authoritative host metadata proves otherwise. Never invent a Foundry Gemini route, infer a provider from an unprefixed model name, silently remove a provider prefix, or substitute a paid model without authorization.

## Execute

1. Keep work requiring five or fewer direct tool calls in the inherited coordinator.
2. Prefer a provider-qualified Astra coordinator selected by the user before Token Mizer. Keep Astra planning and narration compact, avoid repeated reads, and retain hard or high-risk decisions.
3. For substantial bounded implementation or build work, immediately evaluate proactive Gemini eligibility under `token-mizer-budget`. The five-minute capacity threshold does not apply to this proactive route.
4. If Gemini is unavailable, not authorized, or cannot be safely bounded, use `provider.default_model` as the Foundry Sol fallback. Use `provider.bounded_task_model` only for suitable bounded non-build work.
5. After a failed first fix, use `provider.escalation_model` for a bounded rubber-duck diagnosis with the reproduction, evidence, and unresolved question.
6. Delegate self-contained work using `token-mizer-handoff`. Prefer one worker and parallelize only independent work with measurable benefit.

## Policy-gated paid routes

Keep these routes separate:

- **Proactive Gemini builder:** does not require prior Foundry throttling, but does require a valid nonzero policy, authoritative account-wide spend and pending-commitment data, enforceable bounded in-flight spend, a bounded task allowance, and any required explicit approval. User preference authorizes consideration, not unmetered use. Missing, expired, zero-valued, or unenforceable policy blocks Gemini and routes to Foundry Sol.
- **Capacity fallback:** becomes eligible for evaluation only after five minutes of evidenced Foundry rate-limit blocking. It still requires every spending gate above. Proactive Gemini preference must not bypass this threshold for a separate capacity incident.

Each paid decision is valid for one bounded assignment. Record policy eligibility, approval, attempted route, accepted runtime ID, actual observed use, and outcome as separate facts.

## Failure classification and shared capacity

Classify the failure before retrying:

- **Authentication or environment:** credential errors, unavailable Azure CLI tokens, missing login, invalid tenant, or inaccessible credential helpers are not throttling. Stop repeated inference immediately. Report the exact recovery action, such as installing Azure CLI or asking the user to run `az login`, without performing login automatically or exposing tokens. Do not switch to a paid provider.
- **Unavailable provider or model:** rejected connection IDs, unsupported runtime IDs, and missing catalog entries require configuration recovery. Do not infer another provider from the model name. An unavailable Gemini route falls back honestly to Foundry Sol.
- **Capacity contention:** only explicit 429, TPM, quota, capacity, or documented throttling responses qualify. Treat Sol, Astra, and Luna on the same connection as potentially sharing one constrained pool. Switching Foundry models is not a guaranteed workaround.
- **Task failure:** tool, test, or reasoning failures are not provider capacity failures. Follow the normal fix and escalation policy.

For capacity contention, allow one active worker per constrained connection. Reuse the existing session and checkpoints; do not launch duplicate workers, poll, or create replacement sessions. Respect `Retry-After`. Without one, use bounded backoff with no more than three retries and no more than five minutes of cumulative waiting. If the next required delay exceeds that bound, stop instead of ignoring it. The coordinator should yield rather than consume the same provider pool while a worker waits.

If blocked beyond the retry bound, report the evidence and request or await an explicit resume or a genuinely scheduled wake-up. A prompt cannot create an account-wide lock or retry a model call while the agent itself cannot run. Never promise background retries that the host did not schedule.

## Quality

Reproduce reported bugs through the closest feasible user flow before editing. Verify changed behavior and relevant regression checks. Before accepting delegated code, require the same applicable repository-wide gates CI runs, including affected-package coverage for shared changes. Edited-file tests alone are insufficient evidence. Do not add unrelated reviewer agents or demand unrelated full suites. Research claims need sources. Report outcomes, verification, and remaining uncertainty concisely.
