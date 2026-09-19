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
        self.assertEqual("1.6.0", plugin["version"])
        self.assertEqual(plugin["version"], marketplace["metadata"]["version"])
        self.assertEqual(plugin["version"], marketplace["plugins"][0]["version"])

    def test_zero_budget_blocks_gemini_and_paid_fallback(self):
        policy = json.loads(read(POLICY))
        budget = policy["github_budget"]
        for key in (
            "weekday_allowance",
            "weekend_automatic_allowance",
            "task_allowance",
            "user_reported_remaining_approximate",
        ):
            self.assertEqual(0, budget[key])
        self.assertEqual("gemini-3.8-flash", policy["github_models"]["bounded_builder_model"])
        self.assertIn("Zero values keep Gemini and every automatic paid route disabled", policy["notes"])

    def test_pool_is_context_first_and_sol_is_explicit_only(self):
        route = read(ROUTE)
        budget = read(BUDGET)
        agent = read(AGENT)
        for text in (route, budget, agent):
            self.assertIn("gemini-3.8-flash", text)
        self.assertIn("context-fit eligibility before alternation", route)
        self.assertIn("large-context task", route)
        self.assertIn("Sol only on explicit user override", route)
        self.assertIn("Missing, expired, zero-valued, or unenforceable policy blocks Gemini", route)
        self.assertIn("Gemini preference must not bypass that threshold", budget)

    def test_astra_coordinates_and_validates_bounded_builders(self):
        route = read(ROUTE)
        handoff = read(HANDOFF)
        readme = read(README)
        self.assertIn("Astra coordinator", route)
        self.assertIn("current coordinator context", route)
        self.assertIn("applicable repository-wide CI-equivalent checks", handoff)
        self.assertIn("affected-package tests", handoff)
        self.assertIn("edited-file tests alone", handoff)
        self.assertIn("Forge Foundry Astra", readme)

    def test_bounded_rug_is_opt_in_bounded_and_risk_reviewed(self):
        agent = read(AGENT)
        route = read(ROUTE)
        handoff = read(HANDOFF)
        bounded = read(BOUNDED_RUG)
        self.assertIn("only after the user opts the task into that pilot", agent)
        self.assertIn("never changes the default workflow silently", agent)
        self.assertIn("at most one coordinator-diagnosed repair", agent)
        self.assertIn("second failed verification blocks", route)
        self.assertIn("one initial implementation and at most one repair", handoff)
        self.assertIn("substantial, risky, or previously failed work", handoff)
        self.assertIn("Do not create a reviewer for every file", bounded)
        self.assertIn("does not independently prove", bounded)
        self.assertIn("Missing cost remains unknown, never zero", bounded)

    def test_report_distinguishes_proactive_and_capacity_routes(self):
        report = read(REPORT)
        self.assertIn("proactive Gemini 3.8 Flash builders", report)
        self.assertIn("context-fit evidence", report)
        self.assertIn("five-minute-threshold capacity fallback", report)
        self.assertIn("eligibility, approval, attempted model, accepted runtime ID, and actual observed use", report)
        self.assertIn("Do not claim Gemini is faster, cheaper, or better", report)

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
