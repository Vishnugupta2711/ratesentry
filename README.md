# RateSentry — Distributed Rate Limiter & API Gateway

A production-grade distributed rate limiting system built in Python, deployed on AWS.

## What it does

Sits in front of any API and enforces per-user request limits across multiple servers simultaneously. Uses Redis Lua scripting for atomic, race-condition-free distributed state — the same approach used by Google, Stripe, and Cloudflare.

## Architecture
Client → AWS ALB → EC2 Node 1 ─┐

EC2 Node 2 ─┼─→ ElastiCache Redis (shared counter)

EC2 Node 3 ─┘

↓

Prometheus + Grafana

## Benchmark Results

Load tested with Locust — 100 concurrent users, 60 second run:

| Metric | Result |
|---|---|
| Total requests handled | 14,245 |
| Throughput | 237 RPS |
| p50 latency | 70ms |
| p95 latency | 170ms |
| p99 latency | 420ms |
| Rate limit blocks enforced | 5,710 |
| Race conditions | 0 |

## Tech Stack

| Layer | Technology |
|---|---|
| API | Python 3.11 + FastAPI + asyncio |
| Rate limiting | Sliding window algorithm + threading.Lock |
| Distributed state | Redis (sorted sets + Lua atomic scripts) |
| Containerisation | Docker + Docker Compose |
| Cloud compute | AWS EC2 t3.micro (Auto Scaling Group) |
| Managed cache | AWS ElastiCache Redis 7.0 |
| Load balancer | AWS Application Load Balancer |
| Monitoring | Prometheus + Grafana + CloudWatch |
| Load testing | Locust (100 concurrent users) |

## Key Engineering Decisions

**Why Lua scripts in Redis?**
`ZCARD` and `ZADD` must execute as one atomic operation. Two separate calls create a TOCTOU race condition under concurrent load. Lua runs server-side as a single transaction — structurally impossible to interrupt regardless of how many concurrent requests arrive.

**Why sliding window over token bucket?**
Token bucket allows burst at window boundaries — a user can send 100 requests at 11:59:59 and 100 more at 12:00:00, hitting 200 in 2 seconds. Sliding window measures any rolling 60-second span, eliminating this loophole.

**Why asyncio middleware?**
Rate limit checks are I/O bound (Redis round-trip). asyncio cooperative multitasking handles thousands of concurrent connections efficiently without thread overhead.

## Project Structure
ratesentry/

├── app/

│   ├── main.py                 # FastAPI app + middleware

│   ├── rate_limiter.py         # In-memory sliding window (Week 1)

│   └── redis_rate_limiter.py   # Distributed Redis version (Week 2)

├── tests/

│   ├── test_rate_limiter.py        # 6 unit tests

│   └── test_redis_rate_limiter.py  # 6 distributed tests incl. subprocess

├── locustfile.py               # Load test configuration

├── Dockerfile                  # Production container

├── docker-compose.yml          # 3-node local stack

└── requirements.txt

## Running Locally

```bash
# Start full 3-node stack
docker compose up --build

# Hit all 3 nodes
curl http://localhost:8001/api/data -H "X-User-ID: vishnu"
curl http://localhost:8002/api/data -H "X-User-ID: vishnu"
curl http://localhost:8003/api/data -H "X-User-ID: vishnu"

# Run tests
pytest tests/ -v
```

## Test Results
tests/test_rate_limiter.py::test_allows_requests_under_limit         PASSED

tests/test_rate_limiter.py::test_blocks_requests_over_limit          PASSED

tests/test_rate_limiter.py::test_different_users_are_independent     PASSED

tests/test_rate_limiter.py::test_window_expires_and_resets           PASSED

tests/test_rate_limiter.py::test_get_remaining_decreases             PASSED

tests/test_rate_limiter.py::test_concurrent_requests_dont_break_limit PASSED

tests/test_redis_rate_limiter.py::test_redis_allows_under_limit      PASSED

tests/test_redis_rate_limiter.py::test_redis_blocks_over_limit       PASSED

tests/test_redis_rate_limiter.py::test_redis_users_are_independent   PASSED

tests/test_redis_rate_limiter.py::test_redis_window_expires          PASSED

tests/test_redis_rate_limiter.py::test_redis_concurrent_threads      PASSED

tests/test_redis_rate_limiter.py::test_two_separate_processes_share_limit PASSED