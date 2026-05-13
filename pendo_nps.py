import os
import requests
from datetime import datetime, timedelta, timezone

PENDO_API_KEY = os.environ["PENDO_API_KEY"]
SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]
PENDO_GUIDE_ID = os.environ["PENDO_GUIDE_ID"]
PENDO_POLL_ID = os.environ["PENDO_POLL_ID"]
PENDO_FOLLOWUP_POLL_ID = os.environ["PENDO_FOLLOWUP_POLL_ID"]

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


def aggregation_post(pipeline):
    payload = {
        "response": {"mimeType": "application/json"},
        "request": {"pipeline": pipeline}
    }
    r = requests.post(
        f"{PENDO_BASE}/aggregation",
        headers=HEADERS,
        json=payload,
        timeout=30,
    )
    if not r.ok:
        print("Pendo error:", r.status_code, r.text)
        r.raise_for_status()
    return r.json().get("results", [])


def fetch_nps_scores(first_ms, count):
    """Fetch NPS ratings (poll 1)."""
    return aggregation_post([
        {
            "source": {
                "pollEvents": {
                    "guideId": PENDO_GUIDE_ID,
                    "pollId": PENDO_POLL_ID,
                    "blacklist": "apply"
                },
                "timeSeries": {
                    "period": "dayRange",
                    "first": first_ms,
                    "count": count
                }
            }
        },
        {"identified": "visitorId"},
        {"filter": "excluded != true"},
        {
            "select": {
                "visitorId": "visitorId",
                "accountId": "accountId",
                "browserTime": "browserTime",
                "pollResponse": "pollResponse"
            }
        }
    ])


def fetch_followups(first_ms, count):
    """Fetch follow-up text responses (poll 2)."""
    return aggregation_post([
        {
            "source": {
                "pollEvents": {
                    "guideId": PENDO_GUIDE_ID,
                    "pollId": PENDO_FOLLOWUP_POLL_ID,
                    "blacklist": "apply"
                },
                "timeSeries": {
                    "period": "dayRange",
                    "first": first_ms,
                    "count": count
                }
            }
        },
        {"identified": "visitorId"},
        {
            "select": {
                "visitorId": "visitorId",
                "browserTime": "browserTime",
                "followUpResponse": "pollResponse"
            }
        }
    ])


def fetch_nps(start_date, end_date):
    first_ms = to_ms(start_date)
    last_ms = to_ms(end_date)
    count = max(1, (last_ms - first_ms) // (86400 * 1000) + 1)

    scores = fetch_nps_scores(first_ms, count)
    followups = fetch_followups(first_ms, count)

    # Build lookup: visitorId -> followUpResponse (match on closest browserTime)
    followup_map = {}
    for f in followups:
        vid = f.get("visitorId")
        if vid:
            followup_map[vid] = f.get("followUpResponse", "")

    # Merge follow-ups into scores
    results = []
    for s in scores:
        vid = s.get("visitorId")
        score = s.get("pollResponse", 0)
        group = "Promoter" if score >= 9 else ("Passive" if score >= 7 else "Detractor")
        results.append({
            "visitorId": vid,
            "pollResponse": score,
            "responseGroup": group,
            "followUpResponse": followup_map.get(vid, "")
        })

    return results


def compute_nps(results):
    if not results:
        return None, 0, 0, 0, 0
    total = len(results)
    promoters = sum(1 for r in results if r["responseGroup"] == "Promoter")
    passives = sum(1 for r in results if r["responseGroup"] == "Passive")
    detractors = sum(1 for r in results if r["responseGroup"] == "Detractor")
    nps = round(((promoters - detractors) / total) * 100)
    return nps, total, promoters, passives, detractors


def fetch_prior_nps_score(days=7):
    try:
        start, end = date_range(days_ago_start=days * 2, days_ago_end=days)
        results = fetch_nps(start, end)
        nps, *_ = compute_nps(results)
        return nps
    except Exception:
        return None


def get_top_comments(results, n=3):
    with_comments = [
        r for r in results
        if r.get("followUpResponse") and str(r["followUpResponse"]).strip()
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
        text = str(c.get("followUpResponse", "")).strip()
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
    results = fetch_nps(start, end)

    nps, total, promoters, passives, detractors = compute_nps(results)
    if not total:
        print("No NPS responses found for the last 7 days.")
        return

    prior_nps = fetch_prior_nps_score(days=7)
    comments = get_top_comments(results, n=3)

    print(f"NPS: {nps} | Total: {total} | Promoters: {promoters} | Passives: {passives} | Detractors: {detractors}")
    if prior_nps is not None:
        print(f"Prior period NPS: {prior_nps}")

    message = build_slack_message(nps, total, promoters, passives, detractors, prior_nps, comments)
    send_to_slack(message)


if __name__ == "__main__":
    main()
