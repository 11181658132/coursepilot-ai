import unittest

from app.services.telemetry import estimate_cost


class TelemetryTests(unittest.TestCase):
    def test_estimate_cost_includes_tokens_and_file_search(self) -> None:
        cost = estimate_cost(
            {"input_tokens": 10_000, "output_tokens": 1_000},
            input_cost_per_million=0.10,
            output_cost_per_million=0.50,
            file_search_calls=1,
            file_search_cost_per_thousand=2.50,
        )
        self.assertEqual(cost, 0.004)


if __name__ == "__main__":
    unittest.main()
