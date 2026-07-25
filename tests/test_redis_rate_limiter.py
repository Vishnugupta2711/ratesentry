import time
import threading
import subprocess
import sys
from app.redis_rate_limiter import RedisRateLimiter


def make_limiter(max_requests=10, window_seconds=60):
    limiter = RedisRateLimiter(max_requests=max_requests, window_seconds=window_seconds)
    return limiter


def test_redis_allows_under_limit():
    limiter = make_limiter(max_requests=10)
    limiter.reset("test_user_1")

    for _ in range(5):
        assert limiter.is_allowed("test_user_1") is True


def test_redis_blocks_over_limit():
    limiter = make_limiter(max_requests=5)
    limiter.reset("test_user_2")

    for _ in range(5):
        limiter.is_allowed("test_user_2")

    assert limiter.is_allowed("test_user_2") is False


def test_redis_users_are_independent():
    limiter = make_limiter(max_requests=3)
    limiter.reset("user_a")
    limiter.reset("user_b")

    for _ in range(3):
        limiter.is_allowed("user_a")

    assert limiter.is_allowed("user_a") is False
    assert limiter.is_allowed("user_b") is True


def test_redis_window_expires():
    limiter = make_limiter(max_requests=2, window_seconds=1)
    limiter.reset("test_user_3")

    limiter.is_allowed("test_user_3")
    limiter.is_allowed("test_user_3")
    assert limiter.is_allowed("test_user_3") is False

    time.sleep(1.1)
    assert limiter.is_allowed("test_user_3") is True


def test_redis_concurrent_threads():
    """
    100 threads hit Redis simultaneously for the same user.
    Exactly max_requests should be allowed — Lua atomicity guarantees this.
    """
    limiter = make_limiter(max_requests=10)
    limiter.reset("concurrent_user")
    results = []

    def fire():
        results.append(limiter.is_allowed("concurrent_user"))

    threads = [threading.Thread(target=fire) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 10
    assert results.count(False) == 90


def test_two_separate_processes_share_limit():
    """
    THE KEY TEST — proves this is truly distributed.

    Two completely separate Python processes both talk to the same Redis.
    Process 1 uses 8 of the 10 allowed requests.
    Process 2 should only be allowed 2 more, then blocked.

    With the in-memory limiter, this would fail — each process
    has its own memory and neither knows about the other.
    With Redis, they share one counter. This is what distributed means.
    """
    limiter = make_limiter(max_requests=10)
    limiter.reset("shared_user")

    # Process 1 uses 8 requests via a subprocess
    script = """
import sys
sys.path.insert(0, '.')
from app.redis_rate_limiter import RedisRateLimiter
limiter = RedisRateLimiter(max_requests=10, window_seconds=60)
for _ in range(8):
    limiter.is_allowed('shared_user')
print('process_1_done')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True
    )
    assert "process_1_done" in result.stdout, f"Subprocess failed: {result.stderr}"

    # Process 2 (this test) should now only get 2 more
    allowed = 0
    for _ in range(10):
        if limiter.is_allowed("shared_user"):
            allowed += 1

    assert allowed == 2, f"Expected 2 allowed, got {allowed} — distributed state broken"