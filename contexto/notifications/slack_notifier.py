"""Slack Incoming Webhook notifier for ContextO error correlations."""

from __future__ import annotations

import httpx

_SEVERITY_EMOJI = {
    "HIGH": ":red_circle:",
    "MEDIUM": ":large_yellow_circle:",
    "LOW": ":large_green_circle:",
}


def _truncate(text: str, limit: int = 300) -> str:
    text = str(text).strip()
    return text if len(text) <= limit else text[:limit] + "…"


def build_payload(incident: dict) -> dict:
    """Build a Slack Block Kit payload for a new error correlation."""
    trace_id = str(incident.get("trace_id", ""))[:8]
    file_path = incident.get("file_path") or "unknown"
    line_number = incident.get("line_number", "?")
    root_cause = _truncate(incident.get("root_cause") or "", 300)
    user_action = incident.get("user_action") or ""
    severity = str(incident.get("severity") or "LOW").upper()
    sev_emoji = _SEVERITY_EMOJI.get(severity, ":large_green_circle:")

    error_type = root_cause.split(":")[0].strip() if root_cause else "Error"

    header = f":rotating_light: New error correlated — `{error_type}`"
    file_ref = f"`{file_path}:{line_number}`"

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "ContextO — New Error Correlation",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Trace ID*\n`{trace_id}`"},
                {"type": "mrkdwn", "text": f"*File*\n{file_ref}"},
                {"type": "mrkdwn", "text": f"*Severity*\n{sev_emoji} {severity}"},
            ],
        },
    ]

    if user_action:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Triggered by:* {user_action}"},
        })

    if root_cause:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Root cause*\n```{root_cause}```"},
        })

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": header}],
    })

    return {"blocks": blocks}


_DIGEST_MAX_LINES = 15


def build_digest_payload(incidents: list[dict], window_hours: int) -> dict:
    """Build one Block Kit message summarising held-back LOW incidents.

    Incidents are grouped by file so a noisy file reads as one line with a
    count instead of N near-identical entries. Output is capped at
    _DIGEST_MAX_LINES files with an "…and K more" tail so a large backlog can't
    push the section past Slack's 3000-char mrkdwn limit.
    """
    by_file: dict[str, list[dict]] = {}
    for inc in incidents:
        by_file.setdefault(inc.get("file_path") or "unknown", []).append(inc)
    ordered = sorted(by_file.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    lines = []
    for fp, rows in ordered[:_DIGEST_MAX_LINES]:
        latest = rows[-1]
        cause = _truncate(latest.get("root_cause") or "", 120)
        count = f" ×{len(rows)}" if len(rows) > 1 else ""
        lines.append(
            f"• `{fp}:{latest.get('line_number', '?')}`{count} — {cause}"
        )
    if len(ordered) > _DIGEST_MAX_LINES:
        lines.append(f"_…and {len(ordered) - _DIGEST_MAX_LINES} more files_")

    n = len(incidents)
    summary = (
        f"{_SEVERITY_EMOJI['LOW']} *{n} low-severity "
        f"{'incident' if n == 1 else 'incidents'}* across {len(by_file)} "
        f"{'file' if len(by_file) == 1 else 'files'} "
        f"(batched every {window_hours}h)"
    )

    return {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "ContextO — LOW Severity Digest",
                    "emoji": True,
                },
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": summary}},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(lines)[:2900]},
            },
            {"type": "divider"},
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "HIGH/MEDIUM incidents still alert immediately. "
                        "Set LOW_DIGEST_INTERVAL_HOURS=0 to alert on LOW too.",
                    }
                ],
            },
        ]
    }


async def notify_slack(webhook_url: str, incident: dict) -> bool:
    """POST a Slack notification for a new incident. Returns True on success."""
    return await post_slack_payload(webhook_url, build_payload(incident))


async def post_slack_payload(webhook_url: str, payload: dict) -> bool:
    """POST a prebuilt Block Kit payload. Returns True on success."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=payload)
        if resp.status_code != 200:
            print(f"[ContextO] slack: non-200 response ({resp.status_code}): {resp.text[:200]}")
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[ContextO] slack: notification failed — {exc!r}")
        return False
