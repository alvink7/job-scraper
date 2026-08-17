"""Discord webhook notifications.

One embed per job, batched <=10 embeds per request (Discord's limit),
sleeping ~1s between batches. Returns True only if every batch posted OK,
so the caller can decide whether to mark jobs seen.
"""

import json
import time
import urllib.request
import urllib.error

MAX_EMBEDS_PER_REQUEST = 10


def _color(score, min_score):
    if score >= 2 * min_score:
        return 0x2ECC71  # green
    if score >= min_score:
        return 0x3498DB  # blue
    if score >= min_score / 2:
        return 0xF1C40F  # yellow
    return 0xE67E22      # orange


def _truncate(s, n):
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def build_embed(job, min_score):
    partial = job.get("partial", False)
    score = job.get("score", 0)
    title = job.get("title", "(no title)")
    if partial:
        title = "\U0001F7E1 " + title  # yellow circle prefix
    score_str = f"{score}" + (" · partial" if partial else "")
    matched = ", ".join(job.get("matched", []) or [])
    embed = {
        "title": _truncate(title, 256),
        "url": job.get("url", "") or None,
        "color": _color(score, min_score),
        "fields": [
            {"name": "Company", "value": _truncate(job.get("company", "?"), 256),
             "inline": True},
            {"name": "Location",
             "value": _truncate(job.get("location", "") or "—", 256),
             "inline": True},
            {"name": "Score", "value": score_str, "inline": True},
            {"name": "Matched",
             "value": _truncate(matched or "—", 1024), "inline": False},
        ],
    }
    return embed


def _post_batch(webhook_url, embeds):
    payload = {"embeds": embeds}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "jobwatch/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        # Discord returns 204 No Content on success.
        return 200 <= resp.status < 300


def notify(webhook_url, jobs, min_score):
    """Post jobs as Discord embeds. Returns True if all batches succeeded."""
    if not webhook_url:
        print("  [notify] no DISCORD_WEBHOOK_URL set — skipping")
        return False
    if not jobs:
        return True

    embeds = [build_embed(j, min_score) for j in jobs]
    ok = True
    for i in range(0, len(embeds), MAX_EMBEDS_PER_REQUEST):
        batch = embeds[i: i + MAX_EMBEDS_PER_REQUEST]
        try:
            if not _post_batch(webhook_url, batch):
                ok = False
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            print(f"  [notify] HTTP {e.code}: {detail}")
            ok = False
        except Exception as e:  # noqa: BLE001
            print(f"  [notify] ERROR: {e}")
            ok = False
        if i + MAX_EMBEDS_PER_REQUEST < len(embeds):
            time.sleep(1.0)
    return ok
