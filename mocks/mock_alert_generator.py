"""
Fires realistic sample alerts at the running app's /internal/events
endpoint so you can develop and test the full pipeline (parsing ->
fingerprinting -> stats -> triage -> routing -> notification) without
needing production Slack access.

Usage:
    python mocks/mock_alert_generator.py --scenario noisy
    python mocks/mock_alert_generator.py --scenario spike
    python mocks/mock_alert_generator.py --scenario new
    python mocks/mock_alert_generator.py --scenario all
"""
import argparse
import time

import httpx

BASE_URL = "http://localhost:8000"

SAMPLE_MESSAGES = {
    "high_cpu": """[WARNING] High CPU - service-x
service: payments-api
vertical: payments
env: production
host: pod-payments-api-7f9c""",
    "db_conn": """[CRITICAL] Database connection failures
service: payments-db
vertical: payments
env: production
host: db-primary-1""",
    "5xx_spike": """[CRITICAL] API 5xx error rate
service: checkout-api
vertical: payments
env: production
host: pod-checkout-api-3a1b""",
}


def post_event(channel: str, message: str):
    resp = httpx.post(
        f"{BASE_URL}/internal/events",
        json={"channel": channel, "raw_message": message},
        timeout=10,
    )
    resp.raise_for_status()
    print(resp.json())


def scenario_noisy():
    """Fires the same recurring alert many times to trigger 'potential_noise'."""
    for _ in range(25):
        post_event("vertical-a-alerts", SAMPLE_MESSAGES["high_cpu"])
        time.sleep(0.2)


def scenario_spike():
    """Fires a burst to trigger 'high_frequency_spike'."""
    for _ in range(20):
        post_event("vertical-a-alerts", SAMPLE_MESSAGES["5xx_spike"])
        time.sleep(0.1)


def scenario_new():
    """Fires a single novel alert to trigger 'new_unknown'."""
    post_event("vertical-a-alerts", SAMPLE_MESSAGES["db_conn"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=["noisy", "spike", "new", "all"], default="all")
    args = parser.parse_args()

    if args.scenario in ("noisy", "all"):
        print("Running 'noisy' scenario...")
        scenario_noisy()
    if args.scenario in ("spike", "all"):
        print("Running 'spike' scenario...")
        scenario_spike()
    if args.scenario in ("new", "all"):
        print("Running 'new' scenario...")
        scenario_new()
