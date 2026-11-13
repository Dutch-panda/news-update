"""Telegram HTML digest formatter and Bot API sender."""

from __future__ import annotations

import html
import logging
from typing import Any

import requests

from src.formatters import format_item_metrics
from src.models import DigestEdition, NewsItem

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"
TELEGRAM_MAX_CHUNK_CHARS = 3800


def _format_item_html(idx: int, item: NewsItem) -> str:
    safe_title = html.escape(item.title)
    safe_url = html.escape(item.url, quote=True)
    safe_source = html.escape(item.source_name)
    metrics = format_item_metrics(item)

    meta_parts = [f"<i>{safe_source}</i>"]
    if metrics:
        meta_parts.append(html.escape(metrics))
    if item.comments_url and item.comments_url != item.url:
        safe_comments = html.escape(item.comments_url, quote=True)
        meta_parts.append(f'<a href="{safe_comments}">HN</a>')

    lines = [
        f'{idx}. <a href="{safe_url}"><b>{safe_title}</b></a>',
        f"   {' · '.join(meta_parts)}",
    ]
    if item.summary:
        lines.append(f"   {html.escape(item.summary)}")
    return "\n".join(lines)


def render_telegram_chunks(
    edition: DigestEdition,
    site_url: str = "",
    max_chars: int = TELEGRAM_MAX_CHUNK_CHARS,
) -> list[str]:
    """Render a DigestEdition into a standalone header message plus Telegram-safe HTML chunks."""
    active_cats = [
        cat for cat in edition.categories if edition.items_by_category.get(cat.key)
    ]
    topic_summary = ", ".join(html.escape(cat.title) for cat in active_cats)
    header_lines = [
        "━━━━━━━━━━━━━━━━━━━━",
        f"📡 <b>{html.escape(edition.edition_label)}</b>",
        f"🗓 <i>{html.escape(edition.generated_at.strftime('%a, %b %d, %Y · %H:%M %Z'))}</i>",
        f"📊 <i>{edition.total_selected} updates across {len(active_cats)} topic(s)</i>",
    ]
    if topic_summary:
        header_lines.append(f"🏷 {topic_summary}")
    header_lines.append("━━━━━━━━━━━━━━━━━━━━")
    header_msg = "\n".join(header_lines)

    section_blocks: list[list[str]] = []
    for cat in active_cats:
        items = edition.items_by_category.get(cat.key, [])
        cat_header = f"<b>{html.escape(cat.title)}</b>"
        item_blocks = [_format_item_html(i, item) for i, item in enumerate(items, start=1)]
        section_blocks.append([cat_header, *item_blocks])

    if not section_blocks:
        section_blocks.append(["<i>No new unseen updates matching your topics for this slot.</i>"])

    footer = ""
    cleaned_url = (site_url or "").strip()
    if cleaned_url:
        safe_site = html.escape(cleaned_url, quote=True)
        footer = f'📚 <a href="{safe_site}">Browse Full Archive on GitHub Pages</a>'

    chunks: list[str] = [header_msg]
    current = ""

    for blocks in section_blocks:
        full_section = "\n".join(blocks)
        candidate = f"{current}\n\n{full_section}" if current else full_section
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(full_section) <= max_chars:
            current = full_section
            continue

        # Category exceeds max_chars: split cleanly at item boundaries
        cat_header = blocks[0]
        part = cat_header
        for item_block in blocks[1:]:
            if len(f"{part}\n{item_block}") <= max_chars:
                part = f"{part}\n{item_block}"
            else:
                chunks.append(part)
                part = f"{cat_header} (cont.)\n{item_block}"
        current = part

    if footer:
        candidate_footer = f"{current}\n\n{footer}" if current else footer
        if len(candidate_footer) <= max_chars:
            current = candidate_footer
        else:
            if current:
                chunks.append(current)
            current = footer

    if current:
        chunks.append(current)

    return chunks


def send_telegram_messages(
    bot_token: str,
    chat_id: str,
    messages: list[str],
    timeout: int = 15,
) -> list[dict[str, Any]]:
    """Send HTML-formatted message chunks to a Telegram chat."""
    url = TELEGRAM_API_BASE.format(token=bot_token.strip(), method="sendMessage")
    responses: list[dict[str, Any]] = []

    for msg in messages:
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id.strip(),
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=timeout,
        )
        if not resp.ok:
            raise RuntimeError(f"Telegram sendMessage failed ({resp.status_code}): {resp.text}")
        payload = resp.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API error: {payload}")
        responses.append(payload)

    return responses


def fetch_chat_ids(bot_token: str, timeout: int = 15) -> list[dict[str, Any]]:
    """Query Telegram getUpdates to help the user discover their chat ID."""
    url = TELEGRAM_API_BASE.format(token=bot_token.strip(), method="getUpdates")
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram getUpdates error: {payload}")

    discovered: dict[int, dict[str, Any]] = {}
    for update in payload.get("result", []):
        msg = update.get("message") or update.get("channel_post") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is not None:
            discovered[int(chat_id)] = {
                "id": chat_id,
                "type": chat.get("type", ""),
                "title": chat.get("title") or chat.get("username") or chat.get("first_name", ""),
            }
    return list(discovered.values())
