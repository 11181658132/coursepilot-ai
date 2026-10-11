import tempfile
import unittest
from pathlib import Path

from app.services.state import load_state, save_state


class StateTests(unittest.TestCase):
    def test_missing_state_returns_new_independent_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "state.json"
            first = load_state(path)
            first["files"]["a.pdf"] = {"sha256": "abc"}
            second = load_state(path)
            self.assertEqual(second["files"], {})

    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "nested/state.json"
            expected = {
                "version": 1,
                "stores": {"90016:lecture": {"id": "vs_123"}},
                "files": {},
            }
            save_state(path, expected)
            self.assertEqual(load_state(path), expected)


if __name__ == "__main__":
    unittest.main()
