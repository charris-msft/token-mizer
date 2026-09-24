import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "com.github.copilot" / "agents" / "token-mizer.agent.md"
ROUTE = ROOT / "skills" / "token-mizer-route" / "SKILL.md"
HANDOFF = ROOT / "skills" / "token-mizer-handoff" / "SKILL.md"
BUDGET = ROOT / "skills" / "token-mizer-budget" / "SKILL.md"
REPORT = ROOT / "skills" / "token-mizer-report" / "SKILL.md"
BOUNDED_RUG = ROOT / "skills" / "token-mizer-bounded-rug" / "SKILL.md"
README = ROOT / "README.md"
POLICY = ROOT / "examples" / "policy.example.json"
PLUGIN = ROOT / "plugin.json"
MARKETPLACE = ROOT / ".github" / "plugin" / "marketplace.json"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class RoutingPolicyTests(unittest.TestCase):
    def test_release_versions_match(self):
        plugin = json.loads(read(PLUGIN))
        marketplace = json.loads(read(MARKETPLACE))
        self.assertEqual("1.7.0", plugin["version"])
        self.assertEqual(plugin["version"], marketplace["metadata"]["version"])
        self.assertEqual(plugin["version"], marketplace["plugins"][0]["version"])

    def test_public_policy_defaults_off_and_allowlist_is_narrow(self):
        policy = json.loads(read(POLICY))
        budget = policy["github_budget"]
        for key in (
            "weekday_allowance",
            "weekend_automatic_allowance",
            "task_allowance",
            "user_reported_remaining_approximate",
        ):
            self.assertEqual(0, budget[key])
        self.assertIs(False, policy["github_models"]["builtin_uncapped_opt_in"])
        self.assertIs(False, policy["foundry_pilots"]["deepseek_deployment_repair_enabled"])
        self.assertEqual(["gpt-6-sol", "gpt-6-astra", "grok-4.7"],
                         policy["github_models"]["allowed_builtin_models"])
        self.assertIn("no built-in or standing DeepSeek authorization", policy["notes"])
        self.assertIn("Missing/false standing flag requires a new explicit user approval", read(ROUTE))
        self.assertIn("the allocator does not itself read the private policy", read(BUDGET))
        self.assertIn("helper cannot independently verify those host annotations", read(ROUTE))

    def test_pool_is_context_first_and_retired_routes_are_historical_only(self):
        route = read(ROUTE)
        budget = read(BUDGET)
        agent = read(AGENT)
        for text in (route, budget, agent):
            self.assertIn("gpt-6-sol", text)
        self.assertIn("grok-4.7", budget)
        self.assertIn("Gemini 3.8 Flash and GPT-5.6 Sol", route)
        self.assertIn("large-context task", route)
        self.assertIn("explicit *private local* opt-in", route)
        self.assertIn("Missing, expired, zero, or unenforceable data blocks", budget)

    def test_astra_coordinates_and_validates_bounded_builders(self):
        route = read(ROUTE)
        handoff = read(HANDOFF)
        readme = read(README)
        self.assertIn("Built-in GPT-6 Astra", route)
        self.assertIn("inherited coordinator", route)
        self.assertIn("applicable repository-wide CI-equivalent checks", handoff)
        self.assertIn("affected-package tests", handoff)
        self.assertIn("edited-file tests alone", handoff)
        self.assertIn("Foundry DeepSeek-V4.1-Flash", readme)

    def test_bounded_rug_is_opt_in_bounded_and_risk_reviewed(self):
        agent = read(AGENT)
        route = read(ROUTE)
        handoff = read(HANDOFF)
        bounded = read(BOUNDED_RUG)
        self.assertIn("only when the user opts the task into that pilot", agent)
        self.assertIn("optional bounded-RUG mode", agent)
        self.assertIn("at most one diagnosed repair", agent)
        self.assertIn("block if verification fails again", route)
        self.assertIn("one initial implementation and at most one repair", handoff)
        self.assertIn("substantial, risky, or previously failed work", handoff)
        self.assertIn("Do not create a reviewer for every file", bounded)
        self.assertIn("does not independently prove", bounded)
        self.assertIn("Missing cost remains unknown, never zero", bounded)

    def test_report_distinguishes_historical_and_capacity_routes(self):
        report = read(REPORT)
        self.assertIn("retired historical Gemini 3.8 Flash", report)
        self.assertIn("context-fit evidence", report)
        self.assertIn("five-minute-threshold capacity fallback", report)
        self.assertIn("eligibility, local built-in opt-in, approval, attempted model, accepted runtime ID, and actual observed use", report)
        self.assertIn("Do not claim Grok is faster", report)

    def test_documented_bounded_rug_paths_use_copilot_home_fallback(self):
        readme = read(README)
        self.assertIn(
            "$copilotHome = if ($env:COPILOT_HOME) { $env:COPILOT_HOME } else { Join-Path $HOME '.copilot' }",
            readme,
        )
        self.assertIn("$record = Join-Path $copilotHome 'token-mizer\\tasks\\change-42.json'", readme)
        self.assertIn("$registry = Join-Path $copilotHome 'token-mizer\\task-registry.json'", readme)
        self.assertNotIn('$record = "$env:COPILOT_HOME\\token-mizer', readme)

    def test_public_files_do_not_bind_personal_provider_guid(self):
        public_text = "\n".join(
            read(path)
            for path in (
                AGENT, ROUTE, HANDOFF, BUDGET, REPORT, BOUNDED_RUG,
                README, POLICY, PLUGIN, MARKETPLACE, ROOT / "scripts" / "bounded_rug.py",
            )
        )
        self.assertIsNone(
            re.search(
                r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b",
                public_text,
            )
        )


if __name__ == "__main__":
    unittest.main()
