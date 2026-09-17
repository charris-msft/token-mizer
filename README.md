# Token Mizer

Token Mizer is an opt-in GitHub Copilot plugin that uses a Foundry coordinator, policy-authorized Gemini builders, and Foundry fallbacks while minimizing duplicated context, unnecessary calls, and unbounded paid use.

## What it does

- **Astra** is the recommended inherited coordinator for planning, hard decisions, compact delegation, verification, and diagnosis after a failed first fix.
- **Gemini 3.8 Flash** is preferred for substantial bounded implementation and build tasks only when the local paid policy and enforceable spending safeguards authorize it.
- **Sol** is the no-paid-budget or unavailable-Gemini fallback for routine implementation and coordination.
- **Luna** remains available for small bounded non-build work with objective acceptance checks.
- **Terra is not used.**
- Every paid route fails closed unless a valid nonzero local policy, authoritative usage data, and bounded spending controls are available.
- Authentication failures stop with recovery guidance. Only explicit throttling evidence triggers bounded retry behavior.
- The reporting skill automatically handles token-rate, TPM, tokens-per-second, model-throughput, usage, provider-comparison, and routing-effectiveness questions while Token Mizer is selected.

The bundled skills activate automatically only after you select **Token Mizer** or explicitly ask to activate it. Installing or enabling the plugin does not apply routing globally.

## Prerequisites

1. Install a current [GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/plugins-finding-installing) build with plugin support.
2. Configure access to Foundry models in your Copilot host.
3. Copy and complete the local policy example so Token Mizer can map your account-specific provider connection to Sol, Luna, and Astra and can fail closed on paid builders.
4. Select your provider-qualified Forge Foundry Astra model as the coordinator before selecting Token Mizer. Select Foundry Sol instead when you want the economy coordinator.

The public agent profile is model-unpinned. It inherits the coordinator selected by the host and cannot switch the current coordinator automatically. Selecting provider-qualified Foundry Astra during setup prevents accidental GitHub-billed coordinator use while keeping difficult decisions on the orchestrator. Astra plans compact bounded tasks, assigns eligible builds to Gemini, and verifies the returned evidence. Worker routes use either host catalog metadata or the user-confirmed local provider mapping and must be accepted by the runtime. Token Mizer never removes a provider prefix or silently substitutes a paid model.

`gemini-3.8-flash` is treated as GitHub-billed unless authoritative host metadata supplies a provider-qualified ID. Token Mizer preserves a qualified ID the host actually offers and never invents a Foundry Gemini connection. If Gemini is unavailable, not authorized, or cannot be bounded safely, the task stays on Foundry Sol.

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

Set `provider.connection_id` to the Foundry connection ID confirmed for your account and keep the model names aligned with runtime-accepted IDs. The optional `github_models.bounded_builder_model` selects the preferred paid builder; `gemini-3.8-flash` is the current default preference when the host exposes it. Existing policies without this optional object remain valid. Replace the example dates and zero amounts locally. Keep the real file out of source control. Zero values, missing files, invalid files, expired dates, and unavailable enforcement disable Gemini and every automatic paid route while leaving correctly mapped Foundry routing available.

The policy is an allocation, not a live billing system. Token Mizer cannot meter billing, reserve funds, or impose a technical spending cap. Without authoritative usage and bounded-spend enforcement, it remains on Foundry or asks for a specific exception rather than spending automatically. The Gemini preference authorizes proactive consideration for a suitable bounded build, not unmetered use and not an override of a zero-budget period.

## Provider failures and shared capacity

Token Mizer separates authentication and configuration failures from throttling:

- Azure CLI token, login, tenant, credential-helper, provider-ID, and unsupported-model errors stop immediately with actionable recovery guidance. Token Mizer never runs `az login`, exposes tokens, or treats these errors as quota exhaustion.
- Only explicit 429, TPM, quota, capacity, or documented throttling responses trigger retries.
- Sol, Astra, and Luna on one Foundry connection may share capacity. Token Mizer limits a constrained connection to one active worker, reuses the existing session and checkpoints, and does not switch models as a presumed workaround.
- Retries respect `Retry-After` and are bounded to three attempts and five minutes of cumulative waiting. The coordinator yields while waiting instead of polling or competing for the same capacity.
- After the bound, Token Mizer reports the blocker and waits for an explicit or genuinely scheduled resume. Five minutes makes capacity fallback eligible for evaluation, never automatically authorized.
- Proactive Gemini builder selection is a separate route: a suitable bounded build can use it immediately when every paid gate passes. That does not remove the five-minute threshold from a separate capacity fallback.

## Optional bounded-RUG pilot

The bounded-RUG mode is opt-in per task and does not change Token Mizer's default workflow. It keeps work requiring five or fewer direct calls in the coordinator. A larger coherent task may use one constrained worker, followed by deterministic verification. One failed verification permits one Astra-diagnosed repair; a second failure blocks automatic implementation and preserves the checkpoint. Budget, authorization, provider, authentication, and environment blockers stop earlier without consuming the code-repair attempt.

`scripts\bounded_rug.py` stores a versioned private task record atomically and enforces the `task → build → verify → repair → verify → accepted|blocked` bounds. A private registry under `$COPILOT_HOME\token-mizer\task-registry.json` binds each task ID to one canonical record path, and registry-first file locking makes revision checks atomic across resumptions. Use the global `--registry` option to select another private registry for isolated automation or tests. It rejects stale record revisions, replayed events, counter resets, duplicate task identities, invalid transitions, and acceptance without evidence at the selected boundary. The helper validates annotation structure, not whether an evidence reference is true. Keep records and registry files outside the repository and never put credentials, policy values, prompts, or full logs in them.

A practical successful CI-boundary run is:

```powershell
$record = "$env:COPILOT_HOME\token-mizer\tasks\change-42.json"
$registry = "$env:COPILOT_HOME\token-mizer\task-registry.json"
$revision = git rev-parse HEAD
python scripts\bounded_rug.py --registry $registry init --file $record --task-id change-42 --label "Bounded change" --task-class code-change --acceptance-boundary ci-passed --mode bounded-rug --coordinator-session coordinator-session --revision $revision --environment windows --at 2026-09-17T10:00:00Z
python scripts\bounded_rug.py --registry $registry transition --file $record --to build --result started --event-id build-1 --expected-sequence 0 --expected-record-revision 0 --at 2026-09-17T10:01:00Z
python scripts\bounded_rug.py --registry $registry transition --file $record --to verify --result completed --event-id verify-1 --expected-sequence 1 --expected-record-revision 1 --at 2026-09-17T10:02:00Z
python scripts\bounded_rug.py --registry $registry transition --file $record --to accepted --result passed --event-id accept-1 --expected-sequence 2 --expected-record-revision 2 --check "python -m unittest discover -s tests -v" --evidence "ci:run-123" --milestone ci-passed --at 2026-09-17T10:03:00Z
python scripts\bounded_rug.py --registry $registry export-ledger --file $record --output "$env:TEMP\bounded-rug-ledger.json" --analysis-id bounded-rug-pilot --cutoff 2026-09-17T11:00:00Z
python scripts\token_mizer_report.py --start 2026-09-17T10:00:00Z --end 2026-09-17T11:00:00Z --task-ledger "$env:TEMP\bounded-rug-ledger.json" --format json
```

Use `add-scope` and `observe-model` to record every coordinator, builder, reviewer, and validator scope plus provider/model observations. Use literal `unknown` values instead of inference. After a terminal task has actually matured, `record-followup` can record observed reopen and rollback results. Pilot comparisons must match task class and acceptance boundary, count failed attempts, disclose cost and timing coverage, and avoid causal or savings claims. Synthetic runs demonstrate mechanics only; real-world pilot results are pending. Token Mizer never launches paid A/B benchmarks.

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

Routing-effectiveness reports retain Astra/Gemini/Sol/Luna route evidence, failure classes, retries, duplicate workers, paid eligibility versus actual use, first-fix rate, and post-failure escalation. Authentication failures remain separate from capacity evidence. Task KPI priority is request-to-verified-live outcome, with model/tool time separate, p50/p90, all-attempt credits per accepted task, first-pass gate rate, and matured reopened/rollback rates. Every comparison must state its analysis version, cutoff, and evidence coverage.

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
- Marketplace browse shows `token-mizer` version `1.0.0` or newer.
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
scripts/token_mizer_report.py         Read-only usage and throughput helper
tests/test_bounded_rug.py             Bounded transition, persistence, CLI, and reporting tests
tests/test_token_mizer_report.py      Synthetic reporting fixture tests
tests/test_routing_policy.py          Synthetic routing-policy invariants
examples/policy.example.json         Safe, zero-budget local policy template
examples/task-ledger.example.json     Synthetic task annotation template
```

## Privacy and security

This repository contains no personal budget amounts, session-derived private metrics, local user paths, provider connection GUIDs, hooks, external telemetry collection, external services, or automatic updater. Private policy and provider configuration remain local; the report helper reads host-retained session evidence in place and opens databases read-only.

## License

MIT
