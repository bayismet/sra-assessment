"""
SRA production quality monitor.

Runs against live production data between offline eval cycles. Checks four
signals that the offline eval cannot catch: confidence drift, reopen rates,
cost, and knowledge-base freshness.

Returns a structured dict for consumption by alerting infrastructure.
"""

import time
from collections import defaultdict

from platform_sdk import kb, ticketing, traces

CONFIDENCE_THRESHOLD = 0.7
CONFIDENCE_ALERT_RATIO = 0.30  # alert if >30% of scores within 0.1 of threshold
REOPEN_RATE_MULTIPLIER = 2.0   # alert if category exceeds 2x its baseline
COST_P95_ALERT = 3.00          # dollars
KB_STALENESS_DAYS = 7


def check_confidence_distribution(window_days: int = 7) -> dict:
    """Detect confidence scores clustering near the decision threshold."""
    recent = traces.fetch(since_days=window_days, fields=["confidence"])
    scores = [t["confidence"] for t in recent if t.get("confidence") is not None]

    if not scores:
        return {"status": "no_data", "alert": False}

    near_threshold = sum(
        1 for s in scores if abs(s - CONFIDENCE_THRESHOLD) < 0.1
    )
    ratio = near_threshold / len(scores)

    return {
        "status": "ok" if ratio <= CONFIDENCE_ALERT_RATIO else "alert",
        "alert": ratio > CONFIDENCE_ALERT_RATIO,
        "near_threshold_ratio": round(ratio, 3),
        "total_tickets": len(scores),
        "mean_confidence": round(sum(scores) / len(scores), 3),
    }


def check_reopen_rates(window_days: int = 30,
                       baseline_rates: dict | None = None) -> dict:
    """Reopen rate by category. Alerts when any category exceeds 2x baseline."""
    resolved = ticketing.fetch_resolved(since_days=window_days)

    by_category = defaultdict(lambda: {"total": 0, "reopened": 0})
    for t in resolved:
        cat = t.get("category", "unknown")
        by_category[cat]["total"] += 1
        if t.get("reopened"):
            by_category[cat]["reopened"] += 1

    rates = {}
    alerts = []
    for cat, counts in by_category.items():
        rate = counts["reopened"] / counts["total"] if counts["total"] else 0.0
        rates[cat] = round(rate, 3)
        if baseline_rates and cat in baseline_rates:
            if rate > baseline_rates[cat] * REOPEN_RATE_MULTIPLIER:
                alerts.append(cat)

    return {
        "status": "ok" if not alerts else "alert",
        "alert": bool(alerts),
        "rates": rates,
        "alerted_categories": alerts,
    }


def check_cost(window_days: int = 7) -> dict:
    """Per-ticket cost statistics from trace data."""
    recent = traces.fetch(since_days=window_days, fields=["cost"])
    costs = sorted(t["cost"] for t in recent if t.get("cost") is not None)

    if not costs:
        return {"status": "no_data", "alert": False}

    p95_index = int(len(costs) * 0.95)
    p95 = costs[min(p95_index, len(costs) - 1)]

    return {
        "status": "ok" if p95 <= COST_P95_ALERT else "alert",
        "alert": p95 > COST_P95_ALERT,
        "mean": round(sum(costs) / len(costs), 3),
        "p95": round(p95, 3),
        "max": round(costs[-1], 3),
        "total_tickets": len(costs),
    }


def check_kb_freshness() -> dict:
    """Check when the product knowledge base was last refreshed."""
    meta = kb.fetch_product_brain_metadata()
    last_refresh = meta.get("last_refresh_epoch", 0)
    age_days = (time.time() - last_refresh) / 86400

    return {
        "status": "ok" if age_days <= KB_STALENESS_DAYS else "alert",
        "alert": age_days > KB_STALENESS_DAYS,
        "age_days": round(age_days, 1),
        "threshold_days": KB_STALENESS_DAYS,
    }


def run_monitor(baseline_reopen_rates: dict | None = None) -> dict:
    """Run all production quality checks."""
    results = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "checks": {
            "confidence": check_confidence_distribution(),
            "reopen_rates": check_reopen_rates(
                baseline_rates=baseline_reopen_rates
            ),
            "cost": check_cost(),
            "kb_freshness": check_kb_freshness(),
        },
    }
    results["any_alert"] = any(
        c["alert"] for c in results["checks"].values()
    )
    return results
