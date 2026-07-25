import time
import threading
from app.rate_limiter import SlidingWindowRateLimiter


def test_allows_requests_under_limit():
    """Basic case: 5 requests under a limit of 10 should all pass."""
    limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=60)
    for _ in range(5):
        assert limiter.is_allowed("vishnu") is True


def test_blocks_requests_over_limit():
    """Once limit is hit, the next request must be blocked."""
    limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=60)
    for _ in range(5):
        limiter.is_allowed("vishnu")

    # 6th request must be blocked
    assert limiter.is_allowed("vishnu") is False


def test_different_users_are_independent():
    """
    User A hitting their limit must NOT affect User B.
    This is the core fairness guarantee.
    """
    limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60)

    # Exhaust user_a's limit
    for _ in range(3):
        limiter.is_allowed("user_a")

    # user_a is blocked
    assert limiter.is_allowed("user_a") is False

    # user_b is completely unaffected
    assert limiter.is_allowed("user_b") is True


def test_window_expires_and_resets():
    """
    After the time window passes, old requests should expire
    and the user should be allowed again.
    """
    limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=1)

    limiter.is_allowed("vishnu")
    limiter.is_allowed("vishnu")

    # Blocked now
    assert limiter.is_allowed("vishnu") is False

    # Wait for window to expire
    time.sleep(1.1)

    # Should be allowed again
    assert limiter.is_allowed("vishnu") is True


def test_get_remaining_decreases():
    """Remaining count should go down with each request."""
    limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=60)

    assert limiter.get_remaining("vishnu") == 10
    limiter.is_allowed("vishnu")
    assert limiter.get_remaining("vishnu") == 9
    limiter.is_allowed("vishnu")
    assert limiter.get_remaining("vishnu") == 8


def test_concurrent_requests_dont_break_limit():
    """
    50 threads all fire simultaneously for the same user.
    Exactly max_requests should be allowed, not more.
    This tests the threading.Lock is working correctly.
    """
    limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=60)
    results = []

    def fire():
        result = limiter.is_allowed("vishnu")
        results.append(result)

    threads = [threading.Thread(target=fire) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    allowed = results.count(True)
    blocked = results.count(False)

    # Exactly 10 should have been allowed, 40 blocked
    assert allowed == 10, f"Expected 10 allowed, got {allowed}"
    assert blocked == 40, f"Expected 40 blocked, got {blocked}"