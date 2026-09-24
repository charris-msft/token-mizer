---
name: token-mizer-integrate
description: Explicit task-scoped shared routing for a consuming agent such as Deployment Repair. Reuse Token Mizer route, budget and handoff policy without selecting the Token Mizer agent or starting another session.
---

# Shared routing adapter

Use only when the user explicitly invokes a consuming agent that calls this skill, or explicitly requests this integration. Installation alone never activates routing. The consumer retains diagnosis, repair limits, original acceptance target and live proof. This adapter does not run repairs or certify acceptance.

## Stage-load, do not duplicate policy

1. Capture compact inputs: task identity/class/acceptance boundary, original acceptance target, evidence references, checkpoint and observed attempt totals (unknown if absent), complete context estimate, and current capacity/runtime/provider/policy evidence. Keep private values local.
2. Discover actual host-listed skill names for `token-mizer-route`, `token-mizer-budget` and `token-mizer-handoff`; these are repository names, not presumed host IDs. Never invent namespaced IDs or generic plugin dependencies. Plugin installation is separate, not an adapter action.
3. Load canonical route when assessing routing. Load canonical budget when the route needs policy/mapping evidence and before any paid route, fallback or exception. Load canonical handoff only before dependent delegation. Reuse already loaded current instructions, not stale admission. Do not copy policy thresholds into this adapter.
4. A needed dependency absent or unloadable returns `blocked/dependency_missing`. The consumer may collect bounded read-only evidence under its own safety rules, but must not claim missing policy was consumed or start dependent delegation. No permission, context or budget fallback may be invented.
5. Apply canonical eligibility and escalation decisions. A direct Foundry path requires independently established current eligibility, not merely a missing paid route or a small task. Context checks include the inherited coordinator; this instruction-only adapter cannot change it. A large-context task never falls back to Foundry. Retired Gemini/GPT-5.6 Sol routes are history-only; retain canonical built-in Astra escalation and bounded DeepSeek repair-pilot checks.
6. For eligible delegation, use canonical select then fresh admit immediately before the host call. Privately pass the returned `handoff.selected_runtime_id` exactly, never `selected_model`. No cached spawn authorization, spending reservation, hard spend cap or exactly-once external execution is provided. If admission changes, stop and re-evaluate rather than reuse an earlier handoff.

## Result contract

These are **adapter result** fields, not allocator CLI JSON or a new allocator command:

- `status`: exactly one of `direct | handoff_ready | escalation_required | blocked`.
- `reason`: an explicit code from the table below; keep additional explanation local and bounded.
- `task_id`, `task_class`, `acceptance_boundary`, `original_acceptance_target`: preserve the supplied task contract unchanged. Missing required identity/target returns `blocked/input_missing`, not an invented target.
- `evidence_reference`: a safe local reference, or `unknown`.
- `actual_model_label`: observed model family label, or `unknown`; selection is not execution.
- `private_handoff`: only for `handoff_ready`, contains the fresh canonical handoff for immediate operational use. Never publish it or treat it as a durable permission token.

| Status | Reason | Meaning |
|---|---|---|
| direct | context_fits | Canonical policy confirms current coordinator eligibility for this bounded direct task. |
| handoff_ready | current_admission_passed | Canonical handoff loaded and fresh admission succeeded for the exact private runtime. Not proof the host invoked it. |
| escalation_required | first_fix_failed | Canonical diagnosis is needed after failed first fix; consumer still owns repair limits. |
| blocked | dependency_missing | Needed installed skill cannot be discovered or loaded. |
| blocked | input_missing | Task contract or required evidence is absent. |
| blocked | context_unknown_or_insufficient | No established adequate context route. |
| blocked | authorization_denied | Canonical authorization/budget gate failed. |
| blocked | runtime_unavailable | Required runtime/provider route cannot be established or used. |
| blocked | identity_conflict | Current identity/context differs from immutable allocation. |
| blocked | consumer_limit_reached | Consumer repair/safety bound forbids more work. |

If escalation is required but a dependency or eligibility gate blocks it, return `blocked` with that reason; do not launch a substitute model. Retain the original target and checkpoint in all outcomes. The consumer decides and verifies repair acceptance; routing success never changes the acceptance target.

## Evidence and ownership

Shared diagnostics allow only `status`, `reason`, `actual_model_label`, `evidence_reference`. Use a sanitized local reference, not a URL/payload containing private values. Never log private policy values, provider/connection IDs, exact runtime IDs, task payloads or private handoffs in shared diagnostics. Exact runtime identity remains available privately for the operational call.

Token Mizer owns canonical composition fixtures and repository validation; static fixtures and synthetic allocator calls do not prove host discovery, instruction consumption, paid execution or live acceptance. The consuming owner validates actual installed-host discovery/load and a representative flow against the cleared merged release. Do not change consuming repositories, live plugin settings or private policy as part of this adapter.
