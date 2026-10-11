import unittest

from app.services.rate_limit import SlidingWindowRateLimiter


class RateLimiterTests(unittest.TestCase):
    def test_blocks_until_sliding_window_expires(self) -> None:
        now = [100.0]
        limiter = SlidingWindowRateLimiter(2, window_seconds=60, clock=lambda: now[0])
        self.assertIsNone(limiter.check("student"))
        self.assertIsNone(limiter.check("student"))
        self.assertEqual(limiter.check("student"), 60)
        now[0] = 160.0
        self.assertIsNone(limiter.check("student"))

    def test_limits_keys_independently(self) -> None:
        limiter = SlidingWindowRateLimiter(1, clock=lambda: 10.0)
        self.assertIsNone(limiter.check("a"))
        self.assertIsNone(limiter.check("b"))
        self.assertIsNotNone(limiter.check("a"))


if __name__ == "__main__":
    unittest.main()
