import os
import requests
from datetime import datetime, timedelta, timezone

PENDO_API_KEY = os.environ["PENDO_API_KEY"]
SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]
PENDO_GUIDE_ID = os.environ["PENDO_GUIDE_ID"]
PENDO_POLL_ID = os.environ["PENDO_POLL_ID"]

PENDO_BASE = "https://app.pendo.io/api/v1"
HEADERS = {
    "x-pendo-integration-key": PENDO_API_KEY,
    "Content-Type": "application/json",
}


def to_ms(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def date_range(days_ago_start, days_ago_end=0):
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days_ago_start)).strftime("%Y-%m-%d")
    end = (now - timedelta(days=days_ago_end)).strftime("%Y-%m-%d")
    return start, end


def fetch_nps(start_date, end_date):
    payload = {
        "response": {"mimeType": "application/json"},
        "request": {
            "requestId": "NpsResponsesTable",
            "pipeline": [
                {
                    "source": {
                        "npsResponses": {
                            "guideId": PENDO_GUIDE_ID,
                            "pollId": PENDO_POLL_ID
                        },
                        "timeSeries": {
                            "period": "dayRange",
                            "first": to_ms(start_date),
                            "last": to_ms(end_date),
                            "count": -1
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
        timeout=30,
    )
    r.raise_for_status()
    rows = r.json().get("results", [])
    return rows


def parse_results(rows):
    # First row is the summary, remaining rows are individual responses
    summary = {}
    responses = []
    for row in rows:
        if "totalResponsesCount" in row:
            summary = row
        elif "responseGroup" in row:
            responses.append(row)
    return summary, responses


def compute_nps(summary, responses):
    total = summary.get("totalResponsesCount", 0)
    if not total:
        return None, 0, 0, 0, 0
    promoters = sum(1 for r in responses if r.get("responseGroup") == "Promoter")
    passives = sum(1 for r in responses if r.get("responseGroup") == "Passive")
    detractors = sum(1 for r in responses if r.get("responseGroup") == "Detractor")
    nps = round(((promoters - detractors) / total) * 100)
    return nps, total, promoters, passives, detractors


def fetch_prior_nps_score(days=7):
    try:
        start, end = date_range(days_ago_start=days * 2, days_ago_end=days)
        rows = fetch_nps(start, end)
        summary, responses = parse_results(rows)
        nps, *_ = compute_nps(summary, responses)
        return nps
    except Exception:
        return None


def get_top_comments(responses, n=3):
    with_comments = [
        r for r in responses
        if r.get("followUpResponse") and r["followUpResponse"].strip()
    ]
    with_comments.sort(key=lambda r: r.get("pollResponse", 10))
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
        rating = c.get("pollResponse", "?")
        text = c.get("followUpResponse", "").strip()
        comment_blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"> _{text}_\n> Score: *{rating}/10*"}
        })

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📊 Weekly NPS Report — {today}"}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*NPS Score*\n*{nps}* {trend_text}"},
                {"type": "mrkdwn", "text": f"*Total Responses*\n{total}"}
            ]
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"✅ *Promoters* (9–10)\n{promoters} ({pct(promoters)}%)"},
                {"type": "mrkdwn", "text": f"😐 *Passives* (7–8)\n{passives} ({pct(passives)}%)"}
            ]
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"⚠️ *Detractors* (0–6)\n{detractors} ({pct(detractors)}%)"},
                {"type": "mrkdwn", "text": " "}
            ]
        },
        {"type": "divider"}
    ]

    if comment_blocks:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*Recent feedback highlights*"}})
        blocks.extend(comment_blocks)

    return {"blocks": blocks}


def send_to_slack(message):
    r = requests.post(SLACK_WEBHOOK_URL, json=message, timeout=10)
    r.raise_for_status()
    print("✅ Slack message sent.")


def main():
    print("Fetching Pendo NPS data for last 7 days...")
    start, end = date_range(days_ago_start=7)
    rows = fetch_nps(start, end)

    summary, responses = parse_results(rows)
    nps, total, promoters, passives, detractors = compute_nps(summary, responses)

    if not total:
        print("No NPS responses found for the last 7 days.")
        return

    prior_nps = fetch_prior_nps_score(days=7)
    comments = get_top_comments(responses, n=3)

    print(f"NPS: {nps} | Total: {total} | Promoters: {promoters} | Passives: {passives} | Detractors: {detractors}")
    if prior_nps is not None:
        print(f"Prior period NPS: {prior_nps}")

    message = build_slack_message(nps, total, promoters, passives, detractors, prior_nps, comments)
    send_to_slack(message)


if __name__ == "__main__":
    main()
