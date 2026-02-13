"""Send the digest to Discord via webhook."""

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

    max_len = 2000
    # Split by double newline first to avoid breaking mid-section, then by length
    chunks: list[str] = []
    current = []
    current_len = 0

    for part in content.split("\n\n"):
        part = part.strip()
        if not part:
            continue
        # If adding this part would exceed limit, flush current and start new chunk
        if current_len + len(part) + 2 > max_len and current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        # If single part is too long, split by newlines
        if len(part) > max_len:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0
            for line in part.split("\n"):
                if current_len + len(line) + 1 > max_len and current:
                    chunks.append("\n".join(current))
                    current = []
                    current_len = 0
                current.append(line)
                current_len += len(line) + 1
            continue
        current.append(part)
        current_len += len(part) + 2

    if current:
        chunks.append("\n\n".join(current))

    for chunk in chunks:
        payload = {"content": chunk}
        resp = httpx.post(
            config.DISCORD_WEBHOOK_URL,
            json=payload,
            timeout=10.0,
        )
        resp.raise_for_status()
