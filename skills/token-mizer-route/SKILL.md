---
name: token-mizer-route
description: Only for the selected Token Mizer agent or an explicit request to activate Token Mizer. Route substantial work across configured Foundry models, handle rate limits, and escalate difficult decisions.
---

# Foundry-first routing

Apply this skill only while Token Mizer is selected or explicitly activated. Otherwise return without changing routing.

## Routes

Read the private local policy described by `token-mizer-budget`. Resolve a model mapping from either of these sources:

1. The current host catalog explicitly identifies the model as Foundry-backed.
2. The user-confirmed local `provider` object supplies `connection_id`, `name`, `default_model`, `escalation_model`, and `bounded_task_model`.

For a local mapping, form runtime IDs as `provider.connection_id/model-name` unless a configured model is already fully qualified. Use an ID only when the runtime tool accepts it. Provider IDs are account-specific and must remain local. If neither source establishes provider identity, fail closed and report that Foundry routing is not configured.

| Model family | Role |
|---|---|
| Sol | Default worker and routine coordinator |
| Luna | Small, bounded extraction, classification, formatting, or mechanical work with objective checks |
| Astra | Difficult planning, high-risk decisions, and diagnosis after a failed first fix |

No Terra route is configured. Never infer a provider from an unprefixed model name, silently remove a provider prefix, or substitute a GitHub-billed model.

## Execute

1. Keep work requiring five or fewer direct tool calls in the current session.
2. Use `provider.default_model` for ordinary implementation, research, writing, and coordination.
3. Use `provider.bounded_task_model` only when the work is bounded and acceptance is cheap and objective. If unavailable, use the verified default Foundry route once rather than retrying or changing provider.
4. Use `provider.escalation_model` for genuinely difficult or high-risk decisions and bounded rubber-duck diagnosis after a failed first fix. Give it the reproduction, evidence, and unresolved question.
5. Delegate self-contained work using `token-mizer-handoff`. Prefer one worker and parallelize only independent work with measurable benefit.

## Failure classification and shared capacity

Classify the failure before retrying:

- **Authentication or environment:** credential errors, unavailable Azure CLI tokens, missing login, invalid tenant, or inaccessible credential helpers are not throttling. Stop repeated inference immediately. Report the exact recovery action, such as installing Azure CLI or asking the user to run `az login`, without performing login automatically or exposing tokens. Do not switch to a paid provider.
- **Unavailable provider or model:** rejected connection IDs, unsupported runtime IDs, and missing catalog entries require configuration recovery. Do not infer another provider from the model name.
- **Capacity contention:** only explicit 429, TPM, quota, capacity, or documented throttling responses qualify. Treat Sol, Astra, and Luna on the same connection as potentially sharing one constrained pool. Switching models is not a guaranteed workaround.
- **Task failure:** tool, test, or reasoning failures are not provider capacity failures. Follow the normal fix and escalation policy.

For capacity contention, allow one active worker per constrained connection. Reuse the existing session and checkpoints; do not launch duplicate workers, poll, or create replacement sessions. Respect `Retry-After`. Without one, use bounded backoff with no more than three retries and no more than five minutes of cumulative waiting. If the next required delay exceeds that bound, stop instead of ignoring it. The coordinator should yield rather than consume the same provider pool while a worker waits.

After five minutes of actual Foundry rate-limit blocking, a paid fallback becomes eligible for consideration, not authorized. Load `token-mizer-budget` before any paid fallback. If provider identity, authoritative metering, or bounded spending controls are unavailable, remain on Foundry or ask for a specific exception. Prefer a Fast GitHub model variant when paid use is explicitly approved and it can meet the task's quality requirements; use a standard variant only when Fast cannot do the job and state why.

If blocked beyond the retry bound, report the evidence and request or await an explicit resume or a genuinely scheduled wake-up. A prompt cannot create an account-wide lock or retry a model call while the agent itself cannot run. Never promise background retries that the host did not schedule.

## Quality

Reproduce reported bugs through the closest feasible user flow before editing. Verify changed behavior and relevant regression checks. Before merging delegated code, require evidence for the same gates CI runs. Research claims need sources. Report outcomes, verification, and remaining uncertainty concisely.
