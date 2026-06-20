import time
import redis


# The Lua script runs entirely inside Redis as one atomic operation.
# No other request can interrupt between the read (ZCARD) and write (ZADD).
# This is what makes race conditions structurally impossible.
SLIDING_WINDOW_LUA = """
local key          = KEYS[1]
local now          = tonumber(ARGV[1])
local window       = tonumber(ARGV[2])
local max_requests = tonumber(ARGV[3])
local window_start = now - window

-- Remove all timestamps older than the window
redis.call('ZREMRANGEBYSCORE', key, 0, window_start)

-- Count how many requests remain in the window
local count = redis.call('ZCARD', key)

if count < max_requests then
    -- Add this request's timestamp (score=timestamp, member=timestamp)
    redis.call('ZADD', key, now, now)
    -- Auto-expire the key after the window so Redis memory stays clean
    redis.call('EXPIRE', key, window)
    return 1
end

return 0
"""


class RedisRateLimiter:
    """
    Distributed sliding window rate limiter backed by Redis.

    Uses a Lua script for atomic check-and-increment.
    Safe across multiple servers, multiple threads, multiple processes.

    Week 2 upgrade from the in-memory SlidingWindowRateLimiter.
    """

    def __init__(
        self,
        max_requests: int = 100,
        window_seconds: int = 60,
        redis_host: str = "localhost",
        redis_port: int = 6379,
        redis_db: int = 0,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds

        self.client = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            decode_responses=True,
        )

        # Register the Lua script once — Redis returns a callable
        self.lua_check = self.client.register_script(SLIDING_WINDOW_LUA)

    def _key(self, user_id: str) -> str:
        # Each user gets their own sorted set in Redis
        # e.g. "rate_limit:vishnu", "rate_limit:user_42"
        return f"rate_limit:{user_id}"

    def is_allowed(self, user_id: str) -> bool:
        """
        Returns True if the request is allowed, False if blocked.
        One atomic round-trip to Redis — no race conditions possible.
        """
        now = time.time()
        result = self.lua_check(
            keys=[self._key(user_id)],
            args=[now, self.window_seconds, self.max_requests],
        )
        return result == 1

    def get_request_count(self, user_id: str) -> int:
        """How many requests has this user made in the current window."""
        now = time.time()
        window_start = now - self.window_seconds
        return self.client.zcount(self._key(user_id), window_start, now)

    def get_remaining(self, user_id: str) -> int:
        """How many requests does this user have left."""
        return max(0, self.max_requests - self.get_request_count(user_id))

    def reset(self, user_id: str) -> None:
        """Delete a user's counter — useful for testing."""
        self.client.delete(self._key(user_id))