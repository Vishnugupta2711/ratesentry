import time
import os
import hashlib
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from app.redis_rate_limiter import RedisRateLimiter

app = FastAPI(title="RateSentry", version="1.0.0")

# ── CORS ─────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["X-User-ID", "Content-Type"],
)

# ── Redis ─────────────────────────────────────────────────────────────
limiter = RedisRateLimiter(
    max_requests=int(os.getenv("RATE_LIMIT_MAX_REQUESTS", 100)),
    window_seconds=int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", 60)),
    redis_host=os.getenv("REDIS_HOST", "localhost"),
    redis_port=int(os.getenv("REDIS_PORT", 6379)),
)

# ── Strict IP-level limiter (abuse protection) ────────────────────────
# Separate limiter with tighter limits per IP regardless of user ID
ip_limiter = RedisRateLimiter(
    max_requests=int(os.getenv("IP_RATE_LIMIT", 300)),
    window_seconds=60,
    redis_host=os.getenv("REDIS_HOST", "localhost"),
    redis_port=int(os.getenv("REDIS_PORT", 6379)),
)

# ── Prometheus ────────────────────────────────────────────────────────
requests_total = Counter(
    "ratesentry_requests_total",
    "Total requests",
    ["status", "path"]
)
request_latency = Histogram(
    "ratesentry_request_latency_seconds",
    "Request latency",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.5, 1.0]
)
abuse_blocked = Counter(
    "ratesentry_abuse_blocked_total",
    "Requests blocked for abuse"
)

# ── Blocked user agents (bots, scanners) ─────────────────────────────
BLOCKED_UA_FRAGMENTS = [
    "sqlmap", "nikto", "nmap", "masscan", "zgrab",
    "python-requests/2.2", "go-http-client/1.1",
    "curl/7.1", "curl/7.2", "curl/7.3", "curl/7.4",
    "dirbuster", "gobuster", "nuclei", "acunetix",
    "metasploit", "havij", "openvas"
]

# ── Allowed paths (whitelist) ─────────────────────────────────────────
ALLOWED_PATHS = {"/", "/health", "/metrics", "/api/data", "/docs",
                 "/openapi.json", "/favicon.ico"}

def get_client_ip(request: Request) -> str:
    """Extract real IP — handles ALB forwarding."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host

def is_suspicious_ua(ua: str) -> bool:
    """Block known scanner and attack tool user agents."""
    ua_lower = ua.lower()
    return any(frag in ua_lower for frag in BLOCKED_UA_FRAGMENTS)

def get_user_id(request: Request) -> str:
    """
    User identity priority:
    1. X-User-ID header (explicit)
    2. Hashed IP (anonymous users — hashed for privacy)
    """
    explicit = request.headers.get("X-User-ID", "").strip()
    if explicit and len(explicit) <= 64:
        return f"user:{explicit}"
    ip = get_client_ip(request)
    hashed = hashlib.sha256(ip.encode()).hexdigest()[:16]
    return f"ip:{hashed}"

# ── Main middleware ───────────────────────────────────────────────────
@app.middleware("http")
async def security_and_rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    start = time.time()

    # 1. Skip for health + metrics
    if path in ("/health", "/metrics"):
        return await call_next(request)

    # 2. Block unknown paths
    if path not in ALLOWED_PATHS:
        abuse_blocked.inc()
        return JSONResponse(status_code=404, content={"error": "Not found"})

    # 3. Block suspicious user agents
    ua = request.headers.get("User-Agent", "")
    if is_suspicious_ua(ua):
        abuse_blocked.inc()
        return JSONResponse(
            status_code=403,
            content={"error": "Forbidden"},
            headers={"X-Block-Reason": "suspicious-agent"}
        )

    # 4. Block oversized requests (prevent body stuffing attacks)
    content_length = request.headers.get("Content-Length", "0")
    try:
        if int(content_length) > 10_000:
            abuse_blocked.inc()
            return JSONResponse(
                status_code=413,
                content={"error": "Request too large"}
            )
    except ValueError:
        pass

    # 5. IP-level rate limit (hard ceiling per IP regardless of user ID)
    client_ip = get_client_ip(request)
    ip_key = f"ip_raw:{client_ip}"
    if not ip_limiter.is_allowed(ip_key):
        abuse_blocked.inc()
        requests_total.labels(status="ip_blocked", path=path).inc()
        return JSONResponse(
            status_code=429,
            content={
                "error": "IP rate limit exceeded",
                "message": "Too many requests from your IP address",
                "retry_after": 60
            },
            headers={
                "Retry-After": "60",
                "X-Block-Reason": "ip-rate-limit"
            }
        )

    # 6. Per-user rate limit
    user_id = get_user_id(request)
    allowed = limiter.is_allowed(user_id)
    remaining = limiter.get_remaining(user_id)
    duration = time.time() - start
    request_latency.observe(duration)

    if not allowed:
        requests_total.labels(status="blocked", path=path).inc()
        return JSONResponse(
            status_code=429,
            content={
                "error": "Rate limit exceeded",
                "limit": limiter.max_requests,
                "window_seconds": limiter.window_seconds,
                "retry_after": limiter.window_seconds,
                "user_id": user_id,
            },
            headers={
                "X-RateLimit-Limit": str(limiter.max_requests),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(int(time.time()) + limiter.window_seconds),
                "Retry-After": str(limiter.window_seconds),
            }
        )

    requests_total.labels(status="allowed", path=path).inc()
    response = await call_next(request)

    # Inject rate limit headers into every successful response
    response.headers["X-RateLimit-Limit"] = str(limiter.max_requests)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(int(time.time()) + limiter.window_seconds)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"

    return response

# ── Routes ────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "ratesentry", "version": "1.0.0"}

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/api/data")
async def get_data(request: Request):
    user_id = get_user_id(request)
    return {
        "message": "Request successful",
        "user_id": user_id,
        "remaining_requests": limiter.get_remaining(user_id),
        "limit": limiter.max_requests,
        "window_seconds": limiter.window_seconds,
    }

@app.get("/")
async def root():
    return {
        "service": "RateSentry",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
        "metrics": "/metrics",
        "github": "https://github.com/Vishnugupta2711/ratesentry"
    }