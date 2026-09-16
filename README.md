# Token Mizer

Token Mizer is an opt-in GitHub Copilot plugin that routes work across your configured Foundry models while minimizing duplicated context, unnecessary calls, and unbounded paid fallback.

## What it does

- **Sol** is the default for routine implementation, research, writing, and coordination.
- **Luna** handles small, bounded work with objective acceptance checks.
- **Astra** handles difficult planning, high-risk decisions, and diagnosis after a failed first fix.
- **Terra is not used.**
- Paid fallback fails closed unless a valid local policy, authoritative usage data, and bounded spending controls are available.
- Authentication failures stop with recovery guidance. Only explicit throttling evidence triggers bounded retry behavior.
- An optional reporting skill analyzes Token Mizer session logs to compare routing outcomes over time.

The bundled skills activate automatically only after you select **Token Mizer** or explicitly ask to activate it. Installing or enabling the plugin does not apply routing globally.

## Prerequisites

1. Install a current [GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/plugins-finding-installing) build with plugin support.
2. Configure access to Foundry models in your Copilot host.
3. Copy and complete the local policy example so Token Mizer can map your account-specific provider connection to Sol, Luna, and Astra.
4. Select your configured Foundry Sol model as the coordinator before selecting Token Mizer.

The public agent profile is model-unpinned. It inherits the coordinator model selected by the host and cannot switch the current coordinator automatically. Selecting a Foundry coordinator during setup prevents accidental GitHub-billed coordinator use. Worker routes use either host catalog metadata or the user-confirmed local provider mapping, and must still be accepted by the runtime. Token Mizer never removes a provider prefix or silently substitutes a GitHub-billed model.

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

Set `provider.connection_id` to the Foundry connection ID confirmed for your account and keep the model names aligned with runtime-accepted IDs. Replace the example dates and zero amounts locally. Keep the real file out of source control. Zero values, missing files, invalid files, and expired dates disable automatic paid fallback while leaving correctly mapped Foundry routing available.

The policy is an allocation, not a live billing system. Token Mizer cannot meter billing, reserve funds, or impose a technical spending cap. Without authoritative usage and bounded-spend enforcement, it asks for a specific exception rather than spending automatically. If paid GitHub use is explicitly approved, Token Mizer prefers an available Fast variant when it can meet the task's quality requirements.

## Provider failures and shared capacity

Token Mizer separates authentication and configuration failures from throttling:

- Azure CLI token, login, tenant, credential-helper, provider-ID, and unsupported-model errors stop immediately with actionable recovery guidance. Token Mizer never runs `az login`, exposes tokens, or treats these errors as quota exhaustion.
- Only explicit 429, TPM, quota, capacity, or documented throttling responses trigger retries.
- Sol, Astra, and Luna on one Foundry connection may share capacity. Token Mizer limits a constrained connection to one active worker, reuses the existing session and checkpoints, and does not switch models as a presumed workaround.
- Retries respect `Retry-After` and are bounded to three attempts and five minutes of cumulative waiting. The coordinator yields while waiting instead of polling or competing for the same capacity.
- After the bound, Token Mizer reports the blocker and waits for an explicit or genuinely scheduled resume. Five minutes makes paid fallback eligible for evaluation, never automatically authorized.

## Effectiveness report

Ask Token Mizer to run `token-mizer-report` for a dashboard or periodic report. Where the host exposes session history, the skill analyzes Token Mizer sessions using recorded model usage, tool outcomes, durations, retries, and error classes. It compares task categories and routes without treating generic cost multipliers as dollars or claiming causation from small samples. If structured session history is unavailable, it reports the limitation rather than inventing metrics.

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
skills/                              Portable routing, handoff, budget, and reporting skills
examples/policy.example.json         Safe, zero-budget local policy template
```

## Privacy and security

This repository contains no personal budget amounts, local user paths, provider connection GUIDs, hooks, external telemetry collection, external services, or automatic updater. Private policy and provider configuration remain local; the report skill reads host-retained session evidence in place.

## License

MIT
