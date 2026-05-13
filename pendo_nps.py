import os
import json
import time
import requests
from datetime import datetime, timedelta, timezone

PENDO_API_KEY = os.environ["PENDO_API_KEY"]
SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]

PENDO_BASE = "https://app.pendo.io/api/v1"
HEADERS = {
    "x-pendo-integration-key": PENDO_API_KEY,
    "Content-Type": "application/json",
}


def get_time_range(days=7):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    return int(start.timestamp() * 1000), int(now.timestamp() * 1000)


def fetch_nps_responses(start_ms, end_ms):
    payload = {
        "response": {
            "mimeType": "application/json"
        },
        "request": {
            "pipeline": [
                {
                    "source": {
                        "npsResponses": {
                            "timeSeries": {
                                "first": start_ms,
                                "last": end_ms,
                                "count": 30,
                                "granularity": "dayRange"
                            }
                        }
                    }
                }
            ]
        }
    }

    r = requests.post(
        f"{PENDO_BASE}/aggregation",
        headers=HEADERS,
        json=payload,
        timeout=30
    )
    r.raise_for_status()
    return r.json()


def fetch_prior_nps_score(days=7):
    """Fetch NPS score for the prior period for trend comparison."""
    now = datetime.now(timezone.utc)
    end = now - timedelta(days=days)
    start = end - timedelta(days=days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    try:
        data = fetch_nps_responses(start_ms, end_ms)
        results = data.get("results", [])
        scores = [r["npsRating"] for r in results if "npsRating" in r]
        if not scores:
            return None
        promoters = sum(1 for s in scores if s >= 9)
        detractors = sum(1 for s in scores if s <= 6)
        total = len(scores)
        return round(((promoters - detractors) / total) * 100)
    except Exception:
        return None


def compute_nps(results):
    scores = [r["npsRating"] for r in results if "npsRating" in r]
    if not scores:
        return None, 0, 0, 0, 0

    total = len(scores)
    promoters = sum(1 for s in scores if s >= 9)
    passives = sum(1 for s in scores if 7 <= s <= 8)
    detractors = sum(1 for s in scores if s <= 6)
    nps = round(((promoters - detractors) / total) * 100)
    return nps, total, promoters, passives, detractors


def get_top_comments(results, n=3):
    """Return top n low-score comments (detractors), then passives, then promoters."""
    with_comments = [
        r for r in results
        if r.get("npsText", "").strip() and "npsRating" in r
    ]
    with_comments.sort(key=lambda r: r["npsRating"])
    return with_comments[:n]


def build_slack_message(nps, total, promoters, passives, detractors, prior_nps, comments):
    today = datetime.now(timezone.utc).strftime("%b %d, %Y")

    if prior_nps is not None:
        delta = nps - prior_nps
        trend = f"+{delta}" if delta > 0 else str(delta)
        trend_text = f"({trend} vs prior 7 days)"
    else:
        trend_text = ""

    pct = lambda n: round((n / total) * 100) if total else 0

    comment_blocks = []
    for c in comments:
        rating = c.get("npsRating", "?")
        text = c.get("npsText", "").strip()
        comment_blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"> _{text}_\n> Score: *{rating}/10*"
            }
        })

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📊 Weekly NPS Report — {today}"
            }
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*NPS Score*\n*{nps}* {trend_text}"
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Total Responses*\n{total}"
                }
            ]
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"✅ *Promoters* (9–10)\n{promoters} ({pct(promoters)}%)"
                },
                {
                    "type": "mrkdwn",
                    "text": f"😐 *Passives* (7–8)\n{passives} ({pct(passives)}%)"
                }
            ]
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"⚠️ *Detractors* (0–6)\n{detractors} ({pct(detractors)}%)"
                },
                {"type": "mrkdwn", "text": " "}
            ]
        },
        {"type": "divider"}
    ]

    if comment_blocks:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Recent feedback highlights*"
            }
        })
        blocks.extend(comment_blocks)

    return {"blocks": blocks}


def send_to_slack(message):
    r = requests.post(SLACK_WEBHOOK_URL, json=message, timeout=10)
    r.raise_for_status()
    print("✅ Slack message sent.")


def main():
    print("Fetching Pendo NPS data for last 7 days...")
    start_ms, end_ms = get_time_range(days=7)

    data = fetch_nps_responses(start_ms, end_ms)
    results = data.get("results", [])

    if not results:
        print("No NPS responses found for the last 7 days.")
        return

    nps, total, promoters, passives, detractors = compute_nps(results)
    prior_nps = fetch_prior_nps_score(days=7)
    comments = get_top_comments(results, n=3)

    print(f"NPS: {nps} | Total: {total} | Promoters: {promoters} | Passives: {passives} | Detractors: {detractors}")
    if prior_nps:
        print(f"Prior period NPS: {prior_nps}")

    message = build_slack_message(nps, total, promoters, passives, detractors, prior_nps, comments)
    send_to_slack(message)


if __name__ == "__main__":
    main()
