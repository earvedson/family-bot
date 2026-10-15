"""Send the digest to Discord via webhook."""

import time

import httpx

import config


def send_digest(content: str) -> None:
    """
    Post the digest text to the configured Discord webhook.

    Discord allows up to 2000 characters per message. If content is longer,
    it is split into multiple messages.
    """
    if not config.DISCORD_WEBHOOK_URL:
        raise ValueError("DISCORD_WEBHOOK_URL is not set (check .env or environment)")

    for chunk in split_message("@here\n\n" + content):
        _post(chunk)


def split_message(content: str, max_len: int = 2000) -> list[str]:
    """
    Split content into chunks of at most max_len characters, preferring paragraph
    (blank-line) boundaries, then line boundaries for an oversized paragraph, and
    hard-cutting only a single line that is itself too long.
    """
    # Units are packed greedily and joined with "\n\n". An oversized paragraph is
    # pre-packed into line groups (joined with "\n") so its lines keep single
    # newlines and the length accounting stays exact.
    units: list[str] = []
    for part in content.split("\n\n"):
        part = part.strip()
        if not part:
            continue
        if len(part) <= max_len:
            units.append(part)
            continue
        group: list[str] = []
        group_len = 0
        for line in part.split("\n"):
            while len(line) > max_len:
                units.append(line[:max_len])
                line = line[max_len:]
            if group and group_len + 1 + len(line) > max_len:
                units.append("\n".join(group))
                group, group_len = [], 0
            group_len += len(line) + (1 if group else 0)
            group.append(line)
        if group:
            units.append("\n".join(group))

    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + 2 + len(unit) > max_len:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{unit}" if current else unit
    if current:
        chunks.append(current)
    return chunks


def _post(chunk: str, max_attempts: int = 3) -> None:
    payload = {
        "content": chunk,
        "allowed_mentions": {"parse": ["everyone"]},
    }
    for attempt in range(1, max_attempts + 1):
        resp = httpx.post(config.DISCORD_WEBHOOK_URL, json=payload, timeout=10.0)
        if resp.status_code == 429 and attempt < max_attempts:
            # Webhook rate limit: Discord says how long to wait.
            try:
                wait = float(resp.json().get("retry_after", 1.0))
            except ValueError:
                wait = 1.0
            time.sleep(min(wait, 30.0))
            continue
        if resp.is_error:
            raise RuntimeError(
                f"Discord returned {resp.status_code} for a {len(chunk)}-char message: {resp.text[:300]}"
            )
        return
