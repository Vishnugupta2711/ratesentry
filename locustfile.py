from locust import HttpUser, task, between
import random

class RateSentryUser(HttpUser):
    # Each simulated user waits 0.1-0.5s between requests
    # This creates realistic burst traffic
    wait_time = between(0.1, 0.5)

    @task(3)
    def api_data(self):
        """Main endpoint — 3x more likely to be called"""
        user_id = f"user_{random.randint(1, 50)}"
        self.client.get(
            "/api/data",
            headers={"X-User-ID": user_id},
            name="/api/data"
        )

    @task(1)
    def health_check(self):
        """Health endpoint — occasional check"""
        self.client.get("/health", name="/health")
