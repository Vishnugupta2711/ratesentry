import time
import threading
from collections import deque


class SlidingWindowRateLimiter:
    """
    In-memory sliding window rate limiter.
    Thread-safe using threading.Lock.

    This is Week 1 — pure Python, no Redis yet.
    Week 2 will replace this with a distributed Redis version.
    """

    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds

        # Each user gets their own deque of request timestamps
        # { "user_id": deque([timestamp1, timestamp2, ...]) }
        self.user_windows: dict[str, deque] = {}

        # Lock ensures only one thread modifies the data at a time
        # This prevents race conditions in multithreaded environments
        self.lock = threading.Lock()

    def is_allowed(self, user_id: str) -> bool:
        """
        Check if a request from user_id is allowed.
        Returns True if allowed, False if rate limit exceeded.
        """
        now = time.time()
        window_start = now - self.window_seconds

        with self.lock:
            # Create a new deque for this user if first request
            if user_id not in self.user_windows:
                self.user_windows[user_id] = deque()

            user_deque = self.user_windows[user_id]

            # Remove all timestamps that have expired (older than window)
            while user_deque and user_deque[0] < window_start:
                user_deque.popleft()

            # Check if under the limit
            if len(user_deque) < self.max_requests:
                user_deque.append(now)   # record this request
                return True              # allow

            return False                 # block

    def get_request_count(self, user_id: str) -> int:
        """
        Returns how many requests user_id has made in the current window.
        Useful for debugging and the Grafana dashboard later.
        """
        now = time.time()
        window_start = now - self.window_seconds

        with self.lock:
            if user_id not in self.user_windows:
                return 0

            user_deque = self.user_windows[user_id]

            # Count only the non-expired timestamps
            return sum(1 for ts in user_deque if ts >= window_start)

    def get_remaining(self, user_id: str) -> int:
        """
        Returns how many requests user_id has left in the current window.
        This goes into the X-RateLimit-Remaining response header later.
        """
        return max(0, self.max_requests - self.get_request_count(user_id))