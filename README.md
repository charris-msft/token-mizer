# Token Mizer

Token Mizer is an opt-in GitHub Copilot plugin that routes by verified context, host authorization, and a private local policy. The current preference is selected built-in GitHub models, with an independent Foundry Luna economy route and a bounded Foundry DeepSeek deployment-repair pilot.

## What it does

- **Built-in GPT-6 Sol** is preferred for ordinary substantial eligible work after explicit private opt-in.
- **Built-in GPT-6 Astra** handles evidenced hard diagnosis or a failed first fix. **Built-in Grok 4.7** is considered only for explicitly urgent work; preference is not a latency benchmark.
- **Foundry DeepSeek-V4.1-Flash** is an explicitly approved deployment-repair pilot with one bounded implementation attempt per task, original reproduction, full applicable CI, and live verification. A user-approved standing private-local flag can cover future eligible tasks without asking again. Unknown capacity or authorization blocks it; a failed attempt escalates to Astra instead of spawning another DeepSeek attempt.
- **Foundry Luna** is an independently eligible economy option when its context fits, not a substitute when a large-context route is blocked.
- **Gemini 3.8 Flash and GPT-5.6 Sol** are retired for fresh allocation and admission; old route evidence remains readable.
- Context fit precedes preference, using host-reported capacity and a total estimate covering instructions, tool schemas, history, evidence, output, and headroom.
- **Terra is not used.**
- The public built-in opt-in defaults off. Explicit private approval enables uncapped *automatic* routing only for GPT-6 Sol, GPT-6 Astra and Grok 4.7, without inventing a hard spending limit. Other paid routes retain their dated usage and enforcement gate.
- Authentication failures stop with recovery guidance. Only explicit throttling evidence triggers bounded retry behavior.
- The reporting skill automatically handles token-rate, TPM, tokens-per-second, model-throughput, usage, provider-comparison, and routing-effectiveness questions while Token Mizer is selected.

The bundled skills activate only after you select **Token Mizer**, explicitly ask to activate it, or explicitly invoke a consuming agent that calls `token-mizer-integrate` for its current task. Shared invocation needs no Token Mizer agent/session. Installing or enabling the plugin does not apply routing globally.

## Prerequisites

1. Install a current [GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/plugins-finding-installing) build with plugin support.
2. Confirm host access and actor permission for the built-in models you intend to use. Configure Foundry access separately if using Luna or the DeepSeek pilot.
3. Copy and complete the local policy example. Keep its built-in opt-in false unless the user explicitly authorizes these three models; map the account-specific Foundry connection privately.
4. Select an eligible built-in GPT-6 Sol coordinator before selecting Token Mizer, or a verified Foundry Luna coordinator when you want an economy option.

The public agent profile is model-unpinned. It inherits the host-selected coordinator and cannot switch that runtime automatically. It checks the coordinator's context before overflow; if unsuitable, it recommends or starts an authorized GitHub session with a compact faithful handoff. Routing a child does not enlarge the parent. Foundry routes need a verified host catalog entry or a private, user-confirmed provider mapping and an accepted runtime ID. Built-in routes need authoritative host identity and the local opt-in. Never remove a provider prefix or silently substitute a paid model.

A bare `gpt-6-sol`, `gpt-6-astra`, or `grok-4.7` is treated as GitHub-billed absent authoritative contrary metadata. Unknown host capacity is never treated as fit. A large-context task does not silently fall back to a smaller Foundry route: it blocks with the missing capacity or authorization, or uses a lossless bounded decomposition.

## Install

Register this repository as a marketplace, then install the plugin:

```powershell
copilot plugin marketplace add charris-msft/token-mizer
copilot plugin marketplace browse token-mizer-marketplace
copilot plugin install token-mizer@token-mizer-marketplace
copilot plugin list --json
```

Use the marketplace installation only. Direct repository, URL, and path installs are always enabled in current CLI builds, so they defeat Token Mizer's required enable and disable controls.

## Use

1. Start a new Copilot CLI or Copilot App session.
2. Open the agent picker. In Copilot CLI, run `/agent`.
3. Select **Token Mizer**. Its CLI identifier is typically `token-mizer:token-mizer`.
4. Submit work normally. Its bundled routing, handoff, and budget skills are loaded only for the selected agent.

You can also start the CLI with the agent selected:

```powershell
copilot --agent token-mizer:token-mizer
```

The custom agent is intentionally user-invocable and excluded from automatic model invocation. This keeps routing opt-in.

## Manage

```powershell
# Update the marketplace catalog and installed plugin
copilot plugin marketplace update token-mizer-marketplace
copilot plugin update token-mizer@token-mizer-marketplace

# Temporarily remove Token Mizer from future discovery
copilot plugin disable token-mizer@token-mizer-marketplace
copilot plugin enable token-mizer@token-mizer-marketplace

# Remove it completely
copilot plugin uninstall token-mizer@token-mizer-marketplace
```

Use a new session after install, update, enable, or disable. Existing conversations can retain already-loaded instructions.

## Optional local budget policy

Token Mizer never publishes or initializes your budget. To create a local policy, copy [`examples/policy.example.json`](examples/policy.example.json) to:

- `$env:COPILOT_HOME\token-mizer\policy.json` when `COPILOT_HOME` is set
- otherwise, your Copilot configuration directory under `token-mizer\policy.json`

Set `provider.connection_id` to the Foundry connection ID confirmed for your account, if applicable. Only explicit user approval in the private local policy may change `github_models.builtin_uncapped_opt_in` to `true`. The allowed list is exactly `gpt-6-sol`, `gpt-6-astra`, `grok-4.7`; editing the example list does not expand the allocator's allowlist. Missing/false opt-in blocks their automatic use. Existing private policies without this flag do not imply approval. Keep the real file out of source control.

For the bounded Foundry repair pilot, a prior explicit user selection can authorize `foundry_pilots.deepseek_deployment_repair_enabled: true` in the **private** local policy. This is standing approval for one DeepSeek implementation attempt per *new* eligible deployment-repair task; it avoids asking for approval again on each task. The public default is false. When absent/false, obtain explicit approval for that task or block. The caller uses `intent_evidence.source: "user"` and a safe `evidence_reference` to the original approval (the same reference may cover different task IDs); `provider.repair_pilot_model` is only a model mapping, not authorization. The allocator checks the intent annotation and one-attempt/task-ID bound, but does **not** read the private flag itself: the caller must verify the genuine approval and flag before constructing a standing intent, and still recheck host model availability, known sufficient capacity, accepted runtime, actor permission, role, original reproduction, applicable CI and live verification at dispatch.

This opt-in is a user authorization for uncapped routing of those three built-ins, **not** a dollar ceiling, billing meter, reservation or change to actor-level permission. The allocator rereads the private `github_models` object on built-in select, resume and admission; it requires exactly the true opt-in and the three-model allowlist, blocks missing/contradictory fields (including a leftover v1.6 `bounded_builder_model` key – remove it from the private policy when opting in), and permits only bare exact built-in runtime IDs for fresh routes. A qualified GitHub ID cannot be independently proven by this local helper and is rejected, even with a caller-supplied `source: "host"` annotation. The caller must still independently verify the host's current provider identity, runtime acceptance, actor permission, context and availability. Other paid routes still require a valid dated budget, authoritative usage and enforceable in-flight limits; missing/expired/zero values block those routes. The allocator stores immutable task identity, eligibility and event evidence. A resumed v5 assignment keeps its route and must pass fresh admission; a v4 historical assignment can be exported or recorded but cannot be freshly selected or admitted.

## Provider failures and shared capacity

Token Mizer separates authentication and configuration failures from throttling:

- Azure CLI token, login, tenant, credential-helper, provider-ID, and unsupported-model errors stop immediately with actionable recovery guidance. Token Mizer never runs `az login`, exposes tokens, or treats these errors as quota exhaustion.
- Only explicit 429, TPM, quota, capacity, or documented throttling responses trigger retries.
- Luna and DeepSeek on one Foundry connection may share capacity. Token Mizer limits a constrained connection to one active worker, reuses the existing session and checkpoints, and does not switch models as a presumed workaround.
- Retries respect `Retry-After` and are bounded to three attempts and five minutes of cumulative waiting. The coordinator yields while waiting instead of polling or competing for the same capacity.
- After the bound, Token Mizer reports the blocker and waits for an explicit or genuinely scheduled resume. Five minutes makes capacity fallback eligible for evaluation, never automatically authorized.
- An opted-in built-in preference is distinct from the separately gated five-minute capacity fallback.

## Optional bounded-RUG pilot

The bounded-RUG mode is opt-in per task and does not change Token Mizer's default workflow. It keeps work requiring five or fewer direct calls in the coordinator only when its context capacity and route eligibility are established. A larger coherent task may use one constrained worker, followed by deterministic verification. One failed verification permits one Astra-diagnosed repair; a second failure blocks automatic implementation and preserves the checkpoint. Budget, authorization, provider, authentication, and environment blockers stop earlier without consuming the code-repair attempt.

`scripts\model_assignment.py` is also a small public, fail-closed CLI. Run `python scripts\model_assignment.py --help`; each `select`, `admit`, `record`, and `export` action reads one JSON object from stdin (or `--file`) and writes JSON to stdout, with errors on stderr and a nonzero exit. State defaults to `%COPILOT_HOME%\token-mizer\model-assignments.json`, falling back to `%USERPROFILE%\.copilot\token-mizer\model-assignments.json`; pass `--state` for isolated tests. Selection/admission validates supplied provider evidence, authorization, and context annotations before creating or reusing an immutable assignment; outcomes append timestamped evidence events and export supports an exclusive UTC cutoff. An unset or empty `COPILOT_HOME` uses the fallback. See the isolated synthetic example below; never treat example capacity or authorization as live host evidence.

`scripts\bounded_rug.py` stores a versioned private task record atomically and enforces the `task → build → verify → repair → verify → accepted|blocked` bounds. `scripts\model_assignment.py` stores identity-keyed assignments atomically, applies context fit before route preference, and records actual outcomes without remapping on resume. A private registry under `$COPILOT_HOME\token-mizer\task-registry.json` binds each task ID to one canonical record path, with `~\.copilot` as the fallback when `COPILOT_HOME` is unset. Registry-first file locking makes revision checks atomic across resumptions. Use the global `--registry` option to select another private registry for isolated automation or tests. It rejects stale record revisions, replayed events, counter resets, duplicate task identities, invalid transitions, and acceptance without evidence bound to the exact verified revision and environment. Scope completeness is a separate durable attestation that defaults to `unknown`; terminal state never makes it complete. The helper validates annotation structure, not whether an evidence reference is true. Keep records and registry files outside the repository and never put credentials, policy values, prompts, or full logs in them.

The following is a **synthetic annotation-only example**, not a successful CI run: the helper does not execute the named check or verify the invented evidence reference. Use a disposable `COPILOT_HOME` directory and no private policy when trying it. In a real run, execute the checks first, use their actual evidence/target, and attest scope only after verifying participant coverage:

```powershell
$copilotHome = if ($env:COPILOT_HOME) { $env:COPILOT_HOME } else { Join-Path $HOME '.copilot' }
$record = Join-Path $copilotHome 'token-mizer\tasks\change-42.json'
$registry = Join-Path $copilotHome 'token-mizer\task-registry.json'
$revision = git rev-parse HEAD
python scripts\bounded_rug.py --registry $registry init --file $record --task-id change-42 --label "Bounded change" --task-class code-change --acceptance-boundary ci-passed --mode bounded-rug --coordinator-session coordinator-session --revision $revision --environment windows --at 2026-09-17T10:00:00Z
python scripts\bounded_rug.py --registry $registry transition --file $record --to build --result started --event-id build-1 --expected-sequence 0 --expected-record-revision 0 --at 2026-09-17T10:01:00Z
$verifiedRevision = git rev-parse HEAD
python scripts\bounded_rug.py --registry $registry transition --file $record --to verify --result completed --event-id verify-1 --expected-sequence 1 --expected-record-revision 1 --target-revision $verifiedRevision --target-environment windows --at 2026-09-17T10:02:00Z
python scripts\bounded_rug.py --registry $registry transition --file $record --to accepted --result passed --event-id accept-1 --expected-sequence 2 --expected-record-revision 2 --check "python -m unittest discover -s tests -v" --evidence "ci:run-123" --milestone ci-passed --target-revision $verifiedRevision --target-environment windows --at 2026-09-17T10:03:00Z
python scripts\bounded_rug.py --registry $registry attest-scope --file $record --status complete --expected-record-revision 3 --at 2026-09-17T10:04:00Z
python scripts\bounded_rug.py --registry $registry export-ledger --file $record --output "$env:TEMP\bounded-rug-ledger.json" --analysis-id bounded-rug-pilot --cutoff 2026-09-17T11:00:00Z
python scripts\token_mizer_report.py --start 2026-09-17T10:00:00Z --end 2026-09-17T11:00:00Z --model-like "%" --task-ledger "$env:TEMP\bounded-rug-ledger.json" --format json
```

Use `add-scope` and `observe-model` to record every coordinator, builder, reviewer, and validator scope plus provider/model observations. Use literal `unknown` values instead of inference. Run `attest-scope --status complete` only after confirming those scopes cover every participant and do not overlap; otherwise leave the default `unknown` or attest `partial`. Efficiency ratios remain suppressed without complete nonempty scope and metric coverage. After a terminal task has actually matured, `record-followup` can record observed reopen and rollback results; observations later than the report cutoff are excluded. Baseline tasks are observational entries in the existing v1.0 ledger, not executions through this bounded treatment helper. Pilot comparisons are separated by mode, task class, and acceptance boundary, count failed attempts, disclose cost and timing coverage, and avoid causal or savings claims. Synthetic runs demonstrate mechanics only; real-world pilot results are pending. Token Mizer never launches paid A/B benchmarks.

## Assignment and evidence contract

Operational assignment JSON is fail-closed: pool candidates perform the **same role** with distinct family/provider/runtime routes. Fresh families are `sol6`, `astra`, `grok` (GitHub/built-in), `luna`, and `deepseek` (Foundry). An explicit runtime is not permission. Context, actor authorization, host availability, role, and exact runtime are checked at selection and admission. GitHub routes require host-sourced provider/family/runtime evidence and the explicit private-local `paid_policy` opt-in; Foundry routes require qualified IDs. Unknown capacity never fits. Grok requires user-sourced urgent evidence; Astra requires evidenced hard diagnosis or first failed fix; DeepSeek requires `path: "deepseek-pilot"`, `task_class: "deployment-repair"`, host-verified `capacity_evidence` bound to runtime and integer capacity, and user approval of one builder attempt with reproduction, CI and live verification. The caller must actually enforce these annotations and verify the host accepts the runtime. One pilot assignment per task ID and one fresh pilot admission are permitted within the durable state. Duplicate routes, mixed roles, mismatched identities and provider spoofing fail closed. Foundry routes never qualify for large-context tasks.

The ordinary flow is `select → admit → caller invokes host → record → export`. Only a fresh `admit` returns a `handoff`; the caller immediately uses its exact `selected_runtime_id`, never `selected_model`. There is no cached spawn command. Admission rechecks current availability, authorization, context, immutable policy/intent and identity, but is not a money reservation or exactly-once execution guarantee. Never reuse a previous handoff after denial or for another call. Resume preserves allocation identity; changed context or explicit identity blocks rather than rerolls. Coordinator inheritance is unchanged.

Allocation metadata is immutable; historical outcomes, admission and counters are reconstructed from retained events strictly before the UTC cutoff. Missing measurements remain unknown (`null` counters), never zero attempts. Outcome counters are cumulative totals (`cumulative: true`), nonnegative non-boolean integers with no decreases. Each outcome requires an immutable `event_id`; identical replay is idempotent and conflicting replay is rejected. Default times are generated under the write lock. Explicit backdated writes are rejected before atomic replacement, leaving prior bytes readable and unchanged. Verification requires evidence plus revision/environment annotations; this does not prove execution or acceptance. Valid v4 states (including retired routes) remain readable/exportable and can append outcome events, but **all** v4 selection and fresh admission is denied without changing the recorded identity. Start a separate v5 state for new assignments. Invalid v4 delta outcomes, cached spawn events or incomplete admission evidence still fail closed.

Optional typed RUG ingestion uses `bounded_rug.py add-assignment --file <record> --assignment-export <as-of-export> --assignment-id <id> --expected-record-revision <revision>`. The export must have an explicit cutoff and match its retained events and the record's task/class/boundary. `attach-rug` attaches reference strings only. Newer snapshots are excluded at older ledger cutoffs, with unknown-evidence coverage reported. Ingest an already reconstructed older allocator export to retain earlier observations. Feed the ledger to the report's `--rug-ledger` option; observations appear in `rug_ingestion`. Ordinary routing does not require RUG.

Regression mapping (synthetic IDs only):

| Contract reproduction | Named regression |
|---|---|
| Built-in preference and independent Luna eligibility | `test_default_sol6_and_independent_luna_without_opt_in` |
| Host capacity, authorization, identity, duplicate routes | `test_context_authorization_and_exact_host_provider_evidence` |
| Urgent Grok and failed-fix Astra require evidence | `test_urgent_and_failed_fix_are_evidenced_and_not_default` |
| Bounded DeepSeek repair pilot and denied/unknown capacity | `test_deepseek_pilot_only_bounded_repair_and_known_capacity` |
| Fresh handoff, revoked policy and immutable identity | `test_fresh_admission_rechecks_policy_identity_and_runtime` |
| v4 export/record readable, fresh use denied | `test_v4_history_read_export_record_and_retired_admission_denied` |
| Retired Gemini and GPT-5.6 Sol reject explicit fresh requests | `test_retired_routes_rejected_even_with_explicit_override` |
| Exact event replay, cutoff and CLI | `test_record_replay_cutoff_cli_and_concurrent_selection` |
| Concurrent pilot IDs/admission cannot reset single-attempt bound | `test_concurrent_same_task_cannot_duplicate_pilot_admission` |
| RUG identity and forged cutoff rejection | `test_typed_assignment_binding_and_forged_cutoff_rejected` |
| Older RUG evidence survives; future snapshots excluded | `test_newer_rug_snapshot_excluded_and_as_of_snapshot_survives` |
| Public CLI through synthetic host, RUG and report | `test_public_cli_assignment_to_rug_and_report_end_to_end` |
| Concurrent distinct IDs and identical event replay | `test_concurrent_distinct_selection_and_same_event_replay` |
| Concurrent same assignment identity | `test_concurrent_cli_selection_is_identity_stable` |

Synthetic isolated CLI example, not live capacity, permission, execution or spending evidence:
```powershell
$state = Join-Path $env:TEMP ('synthetic-assignments-' + [guid]::NewGuid() + '.json')
$request = '{"assignment_id":"demo","task_id":"demo-task","task_class":"code-change","acceptance_boundary":"ci-passed","required_context":50,"candidates":[{"role":"builder","family":"luna","provider":"Foundry","runtime_id":"synthetic-connection/gpt-5.6-luna","available":true,"authorized":true,"context_capacity":100,"route_evidence":{"source":"local","verified":true,"runtime_id":"synthetic-connection/gpt-5.6-luna"}}],"now":"2026-09-17T10:00:00Z"}'
$request | python scripts\model_assignment.py select --state $state
$request | python scripts\model_assignment.py admit --state $state
# A real caller would invoke its discovered host here, using fresh handoff.selected_runtime_id.
# This example makes no host call and records only a synthetic blocked observation.
'{"assignment_id":"demo","event_id":"demo-outcome","outcome":"blocked","attempts":0,"reassignments":0,"cumulative":true,"timestamp":"2026-09-17T10:01:00Z","evidence":["synthetic:no-host-call"]}' | python scripts\model_assignment.py record --state $state
'{"cutoff":"2026-09-17T11:00:00Z"}' | python scripts\model_assignment.py export --state $state
```
The subprocess regression exercises the complete typed path with parsed outputs and a synthetic host stub. Neither that test nor static adapter fixtures prove real host consumption, paid execution, or deployment acceptance.

## Shared consumer integration

An explicitly invoked consuming agent, such as Deployment Repair, can invoke the thin `token-mizer-integrate` skill without selecting Token Mizer or creating another session. This activates shared routing only for that task, not globally. The consumer discovers actual host-listed skill names; installation is a separate step. The adapter stage-loads canonical route/budget/handoff instructions only when needed, does not duplicate policy, and retains the consumer's original acceptance target.

Adapter statuses are `direct`, `handoff_ready`, `escalation_required`, and `blocked`, with reason codes. These are instruction-level adapter results, **not** allocator CLI JSON. Missing dependencies return `blocked/dependency_missing`; read-only diagnosis may continue under consumer safety rules, but dependent delegation may not. The consumer owns diagnosis, repair bounds and live acceptance proof. Shared diagnostics contain only status, reason, actual model label and evidence reference; operational runtime identities and policy details stay private. Repository fixtures check composition contracts only. Installed-host discovery/load and representative-flow validation belong to the consumer against the cleared merged release.

## Model and task efficiency reports

While Token Mizer is selected, requests about token rates, latency, costs, task efficiency, model comparisons, provider comparisons, or routing effectiveness automatically load `token-mizer-report`. Reporting remains opt-in and does not enable routing globally.

The bundled standard-library helper starts with read-only `assistant_usage_events` records instead of searching conversation text:

```powershell
python scripts\token_mizer_report.py `
  --start 2026-09-01T00:00:00Z `
  --end 2026-09-08T00:00:00Z `
  --model-like "%opus%" `
  --format json
```

`--start` is inclusive and `--end` is exclusive. Offset timestamps are normalized to UTC; timezone-free SQLite timestamps are treated as UTC. The default database is `$COPILOT_HOME\session-store.db`, or `~/.copilot/session-store.db` when unset.

The main-agent rate cohort requires a non-null endpoint, positive output, and positive duration. It reports arithmetic and pooled output TPM, output tokens/second, request-duration mean/median/p90, output size, sessions, observed dates, reasoning settings, timing-field coverage, output-length strata, and exclusions. Request duration is not pure decode speed or task completion time. Compare matched task classes, dates, context, settings, reasoning, and output sizes; historical differences are correlation, not proof that one model caused the result.

Recorded cost uses a separate endpoint-present leaf-call cohort that includes zero-output calls. Missing `total_nano_aiu` stays unknown: an all-unknown cohort reports `null`, while partial coverage is labeled as an observed subtotal. One billion nano-AIU equals one AI credit, and [GitHub documents](https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing) one AI credit as a `$0.01 USD` equivalent. The helper labels this as ledger evidence, not an invoice, Foundry price, or policy balance. It never multiplies by request multipliers or double-counts cache/reasoning tokens. When token pricing details exist, exact rational reconciliation reports absent, malformed, incomplete, partial, unpriced, matched, and mismatched coverage; a valid line beside an invalid line is never called fully reconciled.

For task-level analysis, copy the synthetic `examples\task-ledger.example.json`, annotate explicit task and session/time ownership windows, then add:

```powershell
  --task-ledger examples\task-ledger.example.json
```

The ledger distinguishes CI-passed, merged, deployed-but-not-live-verified, deployed-and-live-verified, failed, blocked, and unfinished outcomes. Set one `acceptance_boundaries` entry per comparable task type; for example, deployment can require `deployed-and-live-verified`. Milestones do not imply one another: merged does not prove CI passed, and deployed does not prove merge or live verification. The selected boundary counts only when it is the explicit outcome or appears in `observed_milestones`; otherwise acceptance remains unknown while the attempt cost remains included. Heterogeneous task types are not collapsed into one efficiency ranking. Optional `acceptance_applicable: false` excludes unrelated tasks from that type's numerator and denominator. Multiple workers and mixed models remain separate in each task. The report separates annotated elapsed time, summed inference resource time, and unioned request-active wall time. Task scopes must fit inside the annotated task window, and overlapping ownership is rejected. Credits per accepted task includes all applicable attempt costs and is withheld unless the boundary is explicit, scopes are complete, and every owned leaf call has known recorded cost. Evidence references and outcomes remain user-supplied annotations, not independently verified facts.

Provider attribution is off by default. `--provider-attribution metadata` reads only selected sessions' model-selection metadata, rejects stale or in-request model switches, and labels missing evidence unknown. It never infers provider from API endpoint.

Routing-effectiveness reports retain current built-in/Foundry and historical Gemini/GPT-5.6 Sol route evidence, failure classes, retries, duplicate workers, paid eligibility versus actual use, first-fix rate, and post-failure escalation. Authentication failures remain separate from capacity evidence. Task KPI priority is request-to-verified-live outcome, with model/tool time separate, p50/p90, all-attempt credits per accepted task, first-pass gate rate, and matured reopened/rollback rates. Every comparison must state its analysis version, cutoff, and evidence coverage.

The helper uses read-only SQLite URI mode, schema introspection, parameterized SQL, exact UTC filtering, and no uploads, installs, writes, collection hooks, scheduled jobs, policy changes, or LLM calls. Missing data produces `UNAVAILABLE`, not guessed metrics.

## If Token Mizer is missing from the agent picker

Run these checks in order:

```powershell
copilot plugin list --json
copilot plugin marketplace list
copilot plugin marketplace browse token-mizer-marketplace
copilot plugin update token-mizer@token-mizer-marketplace
```

Then start a fresh session and run `/agent` in Copilot CLI.

Expected results:

- `plugin list` shows one enabled `token-mizer` entry.
- Marketplace browse shows `token-mizer` version `1.7.0` or newer.
- `/agent` lists **Token Mizer**.

If CLI discovery succeeds but the desktop App picker still does not show the agent, restart the App and open a new session. Plugin agents depend on host support and cache refresh behavior. Report the App version, CLI version, `copilot plugin list --json` output, and whether `/agent` sees Token Mizer. Do not treat successful installation alone as proof that a particular App build renders the agent.

If multiple Token Mizer entries exist, uninstall stale direct or old-marketplace copies and keep one marketplace installation. Direct installs cannot be disabled in current CLI builds and are unsuitable for Token Mizer's opt-in contract.

## Compatibility

- **Copilot CLI:** Marketplace installation, plugin management, skills, and `/agent` discovery are supported.
- **Copilot App:** The agent can appear when that App build consumes Copilot CLI plugins. Visibility must be verified in the target build; this repository cannot force an unsupported picker to render plugin agents.
- **GitHub.com and IDEs:** Custom-agent metadata is portable where the host supports it, but CLI marketplace installation is a Copilot CLI feature. Foundry model availability and provider identifiers remain host-specific.

## Package layout

```text
.github/plugin/marketplace.json      Marketplace catalog
plugin.json                          Agent Plugins 1.0 manifest
com.github.copilot/agents/           Copilot-specific agent profile
skills/                              Portable routing, bounded-RUG, handoff, budget, and reporting skills
scripts/bounded_rug.py                Atomic bounded task-state and ledger-export helper
scripts/model_assignment.py           Durable context-first worker-pool assignment helper
scripts/token_mizer_report.py        Read-only usage and throughput helper
tests/test_bounded_rug.py             Bounded transition, persistence, CLI, and reporting tests
tests/test_token_mizer_report.py      Synthetic reporting fixture tests
tests/test_routing_policy.py          Synthetic routing-policy invariants
tests/test_model_assignment.py        Context-fit, strict provider, pilot, legacy and admission tests
examples/policy.example.json         Safe, built-in-opt-in-off local policy template
examples/task-ledger.example.json     Synthetic task annotation template
```

## Privacy and security

This repository contains no personal budget amounts, session-derived private metrics, local user paths, provider connection GUIDs, hooks, external telemetry collection, external services, or automatic updater. Private policy and provider configuration remain local; the report helper reads host-retained session evidence in place and opens databases read-only.

## License

MIT
