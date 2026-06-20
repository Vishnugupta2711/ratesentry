import time
import os
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from app.redis_rate_limiter import RedisRateLimiter

app = FastAPI(title="RateSentry", version="1.0.0")

# ── Redis connection from environment variables ─────────────────────
# Defaults to localhost for local dev.
# In Docker / AWS these get overridden automatically.
limiter = RedisRateLimiter(
    max_requests=int(os.getenv("RATE_LIMIT_MAX_REQUESTS", 100)),
    window_seconds=int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", 60)),
    redis_host=os.getenv("REDIS_HOST", "localhost"),
    redis_port=int(os.getenv("REDIS_PORT", 6379)),
)

# ── Prometheus metrics ───────────────────────────────────────────────
requests_total = Counter(
    "ratesentry_requests_total",
    "Total requests received",
    ["status"]          # labels: allowed / blocked
)

request_latency = Histogram(
    "ratesentry_request_latency_seconds",
    "Request processing latency",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.5, 1.0]
)


# ── Rate limiting middleware ─────────────────────────────────────────
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Skip middleware for health check and metrics endpoints
    if request.url.path in ("/health", "/metrics"):
        return await call_next(request)

    start = time.time()

    # Extract user identity — header first, fallback to IP address
    user_id = request.headers.get("X-User-ID") or request.client.host

    allowed = limiter.is_allowed(user_id)
    remaining = limiter.get_remaining(user_id)

    duration = time.time() - start
    request_latency.observe(duration)

    if not allowed:
        requests_total.labels(status="blocked").inc()
        return JSONResponse(
            status_code=429,
            content={
                "error": "Rate limit exceeded",
                "limit": limiter.max_requests,
                "window_seconds": limiter.window_seconds,
                "retry_after": limiter.window_seconds,
            },
            headers={
                "X-RateLimit-Limit": str(limiter.max_requests),
                "X-RateLimit-Remaining": "0",
                "Retry-After": str(limiter.window_seconds),
            }
        )

    requests_total.labels(status="allowed").inc()
    response = await call_next(request)

    # Inject rate limit info into every successful response header
    response.headers["X-RateLimit-Limit"] = str(limiter.max_requests)
    response.headers["X-RateLimit-Remaining"] = str(remaining)

    return response


# ── Routes ───────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    """ALB health check endpoint — must return 200."""
    return {"status": "ok", "service": "ratesentry"}


@app.get("/metrics")
async def metrics():
    """Prometheus scrapes this endpoint every 15 seconds."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/data")
async def get_data(request: Request):
    """
    Sample protected endpoint.
    In production this would be your actual business logic.
    """
    user_id = request.headers.get("X-User-ID") or request.client.host
    return {
        "message": "Request successful",
        "user_id": user_id,
        "remaining_requests": limiter.get_remaining(user_id),
    }


@app.get("/")
async def root():
    return {
        "service": "RateSentry",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
        "metrics": "/metrics",
    }