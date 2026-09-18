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
| Gemini 3.8 Flash | Preferred policy-authorized GitHub model when estimated context fits and paid gates pass |
| Luna | Foundry fallback for context-fitting bounded work and cheap non-build extraction, classification, formatting, or mechanical work |
| Sol | Never selected automatically; Sol only on explicit user override |

No Terra route is configured. Preserve a provider-qualified ID that the host actually supplies. A bare `gemini-3.8-flash` ID is GitHub-billed unless authoritative host metadata proves otherwise. Never invent a Foundry Gemini route, infer a provider from an unprefixed model name, silently remove a provider prefix, or substitute a paid model without authorization.

## Execute

1. Keep work requiring five or fewer direct tool calls in the inherited coordinator.
2. Estimate total context before delegation: instructions, tool schemas, conversation history, evidence, expected output, and headroom. Check current coordinator context before overflow. Use host-reported capacity when available; unknown capacity is not a fit. Apply this context-fit eligibility before alternation or cost preference.
3. Check the current coordinator too. An instruction-only selected agent cannot change its host model or context window. If the coordinator is unsuitable, recommend or start an authorized GitHub session with a compact, lossless handoff; routing one child does not enlarge the parent.
4. Prefer a provider-qualified Astra coordinator selected by the user before Token Mizer. Keep Astra planning and narration compact, avoid repeated reads, and retain hard or high-risk decisions.
5. For substantial bounded implementation or build work, evaluate proactive Gemini eligibility under `token-mizer-budget` only after context fit. The five-minute capacity threshold does not apply to this proactive route.
6. If both Gemini and Luna are available, authorized, and context-fitting, alternate using the durable assignment helper. If Flash is blocked for a large-context task, block and explain the missing budget or capacity permission. Never silently fall back to Luna or invent a premium route.
7. If only Luna is context-fitting, use it for ordinary work. Sol is never an automatic fallback. Use it only when the user explicitly selects Sol.
8. After a failed first implementation or fix, use `provider.escalation_model` for a bounded rubber-duck diagnosis with the reproduction, evidence, and unresolved question. In the bounded-RUG pilot, that diagnosis may authorize the single repair attempt; a second failed verification blocks further automatic implementation.
9. Delegate self-contained work using `token-mizer-handoff`. Prefer one worker and parallelize only independent work with measurable benefit.

Keep `role`, `family`, `provider`, and `runtime_id` separate. GitHub may use the bare host runtime `gemini-3.8-flash` only with verified host/local metadata; Foundry runtime IDs must be connection-qualified. Allocate an immutable identity, then immediately before delegation admit by revalidating current availability, authorization, context, and exact role/provider/family/runtime. Pass `selected_runtime_id` to spawn. The helper validates annotations only; it does not discover host truth or enforce budget. Record task identity/class/boundary, context-needed and capacity evidence, eligibility exclusions, intended and actual provider/model/runtime, selection reason, attempts, reassignments, and verified outcome. Do not claim randomized or fair comparison when context, authorization, or budget constrained selection.

## Policy-gated paid routes

Keep these routes separate:

- **Proactive Gemini builder:** does not require prior Foundry throttling, but does require a valid nonzero policy, authoritative account-wide spend and pending-commitment data, enforceable bounded in-flight spend, a bounded task allowance, confirmed context fit, and any required explicit approval. User preference authorizes consideration, not unmetered use. Missing, expired, zero-valued, or unenforceable policy blocks Gemini. For large-context work, that block is a hard stop rather than a Luna fallback.
- **Capacity fallback:** becomes eligible for evaluation only after five minutes of evidenced Foundry rate-limit blocking. It still requires every spending gate above. Proactive Gemini preference must not bypass this threshold for a separate capacity incident.

Each paid decision is valid for one bounded assignment. Record policy eligibility, approval, attempted route, accepted runtime ID, actual observed use, and outcome as separate facts.

## Failure classification and shared capacity

Classify the failure before retrying:

- **Authentication or environment:** credential errors, unavailable Azure CLI tokens, missing login, invalid tenant, or inaccessible credential helpers are not throttling. Stop repeated inference immediately. Report the exact recovery action, such as installing Azure CLI or asking the user to run `az login`, without performing login automatically or exposing tokens. Do not switch to a paid provider.
- **Unavailable provider or model:** rejected connection IDs, unsupported runtime IDs, and missing catalog entries require configuration recovery. Do not infer another provider from the model name. An unavailable Gemini route falls back honestly to Foundry Luna for ordinary context-fitting work; large-context work blocks if GitHub capacity is unavailable.
- **Capacity contention:** only explicit 429, TPM, quota, capacity, or documented throttling responses qualify. Treat Sol, Astra, and Luna on the same connection as potentially sharing one constrained pool. Switching Foundry models is not a guaranteed workaround.
- **Task failure:** tool, test, or reasoning failures are not provider capacity failures. Follow the normal fix and escalation policy.

For capacity contention, allow one active worker per constrained connection. Reuse the existing session and checkpoints; do not launch duplicate workers, poll, or create replacement sessions. Respect `Retry-After`. Without one, use bounded backoff with no more than three retries and no more than five minutes of cumulative waiting. If the next required delay exceeds that bound, stop instead of ignoring it. The coordinator should yield rather than consume the same provider pool while a worker waits.

If blocked beyond the retry bound, report the evidence and request or await an explicit resume or a genuinely scheduled wake-up. A prompt cannot create an account-wide lock or retry a model call while the agent itself cannot run. Never promise background retries that the host did not schedule.

## Quality

Reproduce reported bugs through the closest feasible user flow before editing. Verify changed behavior and relevant regression checks. Before accepting delegated code, require the same applicable repository-wide gates CI runs, including affected-package coverage for shared changes. Edited-file tests alone are insufficient evidence. Run deterministic gates first. Add independent review only for substantial, risky, or previously failed work, never universally per file and never when Forge or another validator already provides equivalent evidence. Research claims need sources. Report outcomes, verification, and remaining uncertainty concisely.
