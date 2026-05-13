import os
import requests
from datetime import datetime, timedelta, timezone

PENDO_API_KEY = os.environ["PENDO_API_KEY"]
SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]
PENDO_GUIDE_ID = os.environ["PENDO_GUIDE_ID"]
PENDO_SUB_ID = os.environ["PENDO_SUB_ID"]
PENDO_POLL_ID = os.environ["PENDO_POLL_ID"]

PENDO_BASE = "https://app.pendo.io/api/v1"
HEADERS = {
    "x-pendo-integration-key": PENDO_API_KEY,
    "Content-Type": "application/json",
}


def date_range(days_ago_start, days_ago_end=0):
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days_ago_start)).strftime("%Y-%m-%d")
    end = (now - timedelta(days=days_ago_end)).strftime("%Y-%m-%d")
    return start, end


def fetch_nps(start_date, end_date, include_responses=False):
    payload = {
        "guideId": PENDO_GUIDE_ID,
        "pollId": PENDO_POLL_ID,
        "startTime": start_date,
        "endTime": end_date,
        "period": "dayRange",
    }
    if include_responses:
        payload["includeResponses"] = "unsorted"

    r = requests.post(
        f"{PENDO_BASE}/nps/report",
        headers=HEADERS,
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def fetch_prior_nps_score(days=7):
    try:
        start, end = date_range(days_ago_start=days * 2, days_ago_end=days)
        data = fetch_nps(start, end)
        meta = data.get("metadata", [[]])[0]
        if meta:
            return round(meta[0].get("npsScore", 0))
        return None
    except Exception:
        return None


def compute_nps(data):
    meta = data.get("metadata", [[]])[0]
    if not meta:
        return None, 0, 0, 0, 0
    m = meta[0]
    nps = round(m.get("npsScore", 0))
    total = m.get("numResponses", 0)
    promoters = m.get("numPromoters", 0)
    passives = m.get("numNeutral", 0)
    detractors = m.get("numDetractors", 0)
    return nps, total, promoters, passives, detractors


def get_top_comments(data, n=3):
    responses = data.get("responses", [[]])[0]
    with_comments = [r for r in responses if r.get("npsReason", "") and r["npsReason"].strip()]
    with_comments.sort(key=lambda r: r.get("npsScore", 10))
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
        rating = c.get("npsScore", "?")
        text = c.get("npsReason", "").strip()
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
            "text": {"type": "mrkdwn", "text": "*Recent feedback highlights*"}
        })
        blocks.extend(comment_blocks)

    return {"blocks": blocks}


def send_to_slack(message):
    r = requests.post(SLACK_WEBHOOK_URL, json=message, timeout=10)
    r.raise_for_status()
    print("✅ Slack message sent.")


def main():
    print("Fetching Pendo NPS data for last 7 days...")
    start, end = date_range(days_ago_start=7)

    data = fetch_nps(start, end, include_responses=True)
    nps, total, promoters, passives, detractors = compute_nps(data)

    if not total:
        print("No NPS responses found for the last 7 days.")
        return

    prior_nps = fetch_prior_nps_score(days=7)
    comments = get_top_comments(data, n=3)

    print(f"NPS: {nps} | Total: {total} | Promoters: {promoters} | Passives: {passives} | Detractors: {detractors}")
    if prior_nps is not None:
        print(f"Prior period NPS: {prior_nps}")

    message = build_slack_message(nps, total, promoters, passives, detractors, prior_nps, comments)
    send_to_slack(message)


if __name__ == "__main__":
    main()
