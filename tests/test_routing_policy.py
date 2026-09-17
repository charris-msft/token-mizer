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
        self.assertEqual("1.4.0", plugin["version"])
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

    def test_gemini_preference_is_policy_gated_with_sol_fallback(self):
        route = read(ROUTE)
        budget = read(BUDGET)
        agent = read(AGENT)
        for text in (route, budget, agent):
            self.assertIn("gemini-3.8-flash", text)
        self.assertIn("Missing, expired, zero-valued, or unenforceable policy blocks Gemini", route)
        self.assertIn("routes to Foundry Sol", route)
        self.assertIn("five-minute capacity threshold does not apply to this proactive route", route)
        self.assertIn("Gemini preference must not bypass that threshold", budget)

    def test_astra_coordinates_and_validates_bounded_builders(self):
        route = read(ROUTE)
        handoff = read(HANDOFF)
        readme = read(README)
        self.assertIn("Astra coordinator", route)
        self.assertIn("applicable repository-wide CI-equivalent checks", handoff)
        self.assertIn("affected-package tests", handoff)
        self.assertIn("edited-file tests alone", handoff)
        self.assertIn("Forge Foundry Astra", readme)

    def test_report_distinguishes_proactive_and_capacity_routes(self):
        report = read(REPORT)
        self.assertIn("proactive Gemini 3.8 Flash builders", report)
        self.assertIn("five-minute-threshold capacity fallback", report)
        self.assertIn("eligibility, approval, attempted model, accepted runtime ID, and actual observed use", report)
        self.assertIn("Do not claim Gemini is faster, cheaper, or better", report)

    def test_public_files_do_not_bind_personal_provider_guid(self):
        public_text = "\n".join(
            read(path)
            for path in (AGENT, ROUTE, HANDOFF, BUDGET, REPORT, README, POLICY, PLUGIN, MARKETPLACE)
        )
        self.assertIsNone(
            re.search(
                r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b",
                public_text,
            )
        )


if __name__ == "__main__":
    unittest.main()
