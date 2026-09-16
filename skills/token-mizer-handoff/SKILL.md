---
name: token-mizer-handoff
description: Only for the selected Token Mizer agent or an explicit request to activate Token Mizer. Delegate substantial bounded work with compact context and objective checks.
---

# Lean handoff

Apply only while Token Mizer is selected or explicitly activated. Use `token-mizer-route` first when the route is not established. Explicitly select an identifier that the host confirms belongs to the configured Foundry provider.

## Before spawning

- Keep work requiring five or fewer direct calls in the current session.
- Delegate a self-contained objective, not an open-ended exploration.
- Reuse a suitable worker and its checkpoints for follow-ups.
- Allow only one active worker per Foundry connection when throttling or shared-capacity contention is suspected. Do not launch a Sol, Astra, or Luna replacement on the same connection as a workaround unless the host proves capacity is independent.
- Preserve required specialist agents, skills, permissions, and approvals.
- Never create an unrelated repository session merely to test routing.

## Assignment contract

Provide:

1. **Outcome** - deliverable and acceptance criteria.
2. **Scope** - owned files or bounded question, plus exclusions.
3. **Evidence** - relevant paths, sources, reproduction, errors, and decisions.
4. **Execution** - permitted actions, validation, approvals, and Foundry-only constraint.
5. **Stop** - finish when checks pass; return the blocker after one failed fix rather than looping. Authentication or environment errors stop immediately with recovery guidance. Explicit throttling follows the bounded retry policy in `token-mizer-route`.
6. **Return** - result, artifacts, checks and outcomes, and unresolved risks.

Aim for 500 words or fewer in the assignment and 200 words or fewer in the response unless failure evidence requires more.

## Accept results

Require verifiable artifacts and applicable CI-equivalent checks, not a success claim. Honor running-app verification and merge policies. Do not repeat a successful investigation with another model. While a constrained Foundry worker is waiting, the coordinator yields instead of polling or consuming the same pool. Escalate only the unresolved decision or discrepancy.
