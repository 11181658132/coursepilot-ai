import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.eval_budget import BudgetLedger
from app.live_acceptance import AcceptanceClient, accept, main


class LiveAcceptanceGuards(unittest.TestCase):
    def test_cli_does_not_call_api_without_explicit_live_flag(self):
        with (
            patch("sys.argv", ["acceptance", "--output-dir", "unused"]),
            patch("app.live_acceptance.accept") as run,
            patch("builtins.print"),
        ):
            main()
        run.assert_not_called()

    def test_invalid_budget_is_rejected_before_loading_credentials(self):
        with patch("app.live_acceptance.get_settings") as settings:
            for budget in (0, 0.051, float("nan")):
                with self.assertRaises(ValueError):
                    accept(Path("evals/reports/invalid-budget-guard"), budget)
        settings.assert_not_called()

    def test_unknown_tools_cannot_reach_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = Mock()
            client = AcceptanceClient(provider, BudgetLedger(Path(directory) / "budget.json", 0.05))
            with self.assertRaises(ValueError):
                client.respond(model="gpt-6-luna", tools=[{"type": "function", "name": "delete"}])
            provider.responses.create.assert_not_called()

    def test_uncertain_calls_keep_reservation_and_enforce_shared_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = BudgetLedger(Path(directory) / "budget.json", 0.05)
            provider = SimpleNamespace(
                responses=SimpleNamespace(create=Mock(side_effect=TimeoutError))
            )
            client = AcceptanceClient(provider, ledger)
            for _ in range(2):
                with self.assertRaises(TimeoutError):
                    client.respond(
                        model="gpt-6-luna", tools=[{"type": "function", "name": "search_knowledge"}]
                    )
            from app.eval_budget import BudgetExceeded

            with self.assertRaises(BudgetExceeded):
                client.respond(
                    model="gpt-6-luna", tools=[{"type": "function", "name": "propose_feedback"}]
                )
            self.assertEqual(provider.responses.create.call_count, 2)
            self.assertEqual(ledger.data["uncertain_calls"], 2)
            self.assertAlmostEqual(ledger.data["accounted_usd"], 0.04)
