---
name: token-mizer-bounded-rug
description: Only for the selected Token Mizer agent after an explicit bounded-RUG pilot opt-in. Run one build, deterministic verification, and at most one diagnosed repair with durable acceptance evidence.
---

# Bounded build-verify-repair

Apply only while Token Mizer is selected and the user has opted this task into the bounded-RUG pilot. The normal Token Mizer workflow remains the default.

## Contract

1. Define one coherent task, its class, coordinator/worker/validator scopes, exact acceptance boundary, starting revision, and environment.
2. Keep tasks needing five or fewer direct calls in the coordinator. For substantial work, use one suitable builder on a constrained Foundry connection. Reuse that worker and compact checkpoints.
3. Run one initial implementation, then the deterministic gates for the selected boundary.
4. If verification fails because of code, the Astra coordinator diagnoses the failure as a narrowly bounded rubber duck. Permit at most one repair implementation, then verify again.
5. If the repaired result fails, record `blocked` with both failures and stop automatic implementation attempts. Return the checkpoint and next decision. Never reset counters by resuming or recreating the same task record.
6. Stop earlier for budget, authorization, provider, authentication, or environment constraints. These are blockers, not code-repair attempts. Only explicit throttling follows the routing retry policy.
7. Accept only with evidence for the selected boundary. Unit tests, CI, merge, deployment, and verified running behavior are distinct milestones and never imply one another.

Use `scripts\bounded_rug.py` for runtime-enforced state transitions and atomic private records. Prompt instructions govern agent behavior; the helper enforces only record structure, transition order, stale/replayed events, and the one-build/one-repair bounds. It validates evidence annotations but does not independently prove that an annotation is true.

## Validation and review

Run deterministic repository gates first. Add independent review only for substantial, risky, or previously failed work, and include its session scope in the record. Do not create a reviewer for every file, duplicate Forge or an equivalent validator, or rerun evidence that already proves the same boundary.

## Pilot measurement

Export the record to the existing v1.0 task-ledger format and run `token-mizer-report`. Record baseline and bounded-RUG mode, task class, identical acceptance boundary, all coordinator/builder/reviewer/validator scopes, failed attempts, and provider/model observations including unknowns. The reporter supplies tokens, known credits, elapsed time, model-active time, and coverage. Missing cost remains unknown, never zero. Compare only matched tasks and report subsequent repairs or reopens only when observed. Synthetic runs prove mechanics only; real-world pilot outcomes remain pending until measured. Do not launch paid benchmarks.
