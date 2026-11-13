"""CLI entrypoint for running digests, inspecting Telegram chat IDs, and rebuilding the site."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.classifier import NewsClassifier
from src.config_loader import load_config
from src.dedup import DedupStore
from src.fetchers import enrich_missing_summaries
from src.fetchers.github_trends import fetch_github
from src.fetchers.hackernews import fetch_hackernews
from src.fetchers.rss_feeds import fetch_rss_feeds
from src.formatters.markdown_fmt import save_markdown_and_index
from src.formatters.site_builder import build_site
from src.formatters.telegram_fmt import (
    fetch_chat_ids,
    render_telegram_chunks,
    send_telegram_messages,
)
from src.models import AppConfig, DigestEdition, NewsItem

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DIGESTS_DIR = ROOT_DIR / "digests"
DOCS_DIR = ROOT_DIR / "docs"
SEEN_PATH = DATA_DIR / "seen_items.json"
INDEX_PATH = DATA_DIR / "digests_index.json"

logger = logging.getLogger("news_update")


def resolve_edition_slot(
    local_now: datetime,
    schedule_slots: dict[str, str],
    override: str = "",
) -> tuple[str, str]:
    """Determine (edition_key, edition_label) from override or current local hour."""
    key = (override or "").strip().lower()
    if not key:
        hour = local_now.hour
        if hour < 12:
            key = "morning"
        elif hour < 19:
            key = "afternoon"
        else:
            key = "night"
    label = schedule_slots.get(key, f"{key.title()} Update")
    return key, label


def run_digest(
    config_path: str | None = None,
    dry_run: bool = False,
    no_telegram: bool = False,
    no_dedup: bool = False,
    edition_override: str = "",
) -> int:
    """Execute the full fetch -> dedup -> classify -> save -> site -> Telegram pipeline."""
    config: AppConfig = load_config(config_path)
    tz = timezone(timedelta(hours=config.timezone_offset_hours), name=f"UTC+{config.timezone_offset_hours:02d}:00")
    utc_now = datetime.now(timezone.utc)
    local_now = utc_now.astimezone(tz)

    edition_key, edition_label = resolve_edition_slot(
        local_now=local_now,
        schedule_slots=config.schedule_slots,
        override=edition_override,
    )
    edition_id = local_now.strftime("%Y-%m-%d-%H%M")

    fetched: list[NewsItem] = []
    fetched.extend(
        fetch_hackernews(
            config.hackernews,
            max_summary_chars=config.digest.description_max_chars,
            now=utc_now,
        )
    )
    fetched.extend(
        fetch_github(
            config.github,
            max_summary_chars=config.digest.description_max_chars,
            now=utc_now,
        )
    )
    fetched.extend(
        fetch_rss_feeds(
            config.rss_feeds,
            max_summary_chars=config.digest.description_max_chars,
            lookback_hours=config.rss_lookback_hours,
            now=utc_now,
        )
    )

    dedup_store = DedupStore(SEEN_PATH)
    dedup_store.prune(config.digest.dedup_retention_days, now=utc_now)

    unseen = dedup_store.filter_unseen(fetched, ignore_history=no_dedup)

    classifier = NewsClassifier(config.categories)
    items_by_category = classifier.classify_and_select(unseen, config=config, now=utc_now)

    selected_items = [
        item
        for cat in config.categories
        for item in items_by_category.get(cat.key, [])
    ]
    enrich_missing_summaries(selected_items, max_chars=config.digest.description_max_chars)

    edition = DigestEdition(
        edition_id=edition_id,
        edition_key=edition_key,
        edition_label=edition_label,
        generated_at=local_now,
        categories=config.categories,
        items_by_category=items_by_category,
        total_fetched=len(fetched),
        total_after_dedup=len(unseen),
    )

    md_path, _, _ = save_markdown_and_index(
        edition=edition,
        digests_dir=DIGESTS_DIR,
        index_path=INDEX_PATH,
    )
    index_html = build_site(index_path=INDEX_PATH, docs_dir=DOCS_DIR)

    site_url = os.getenv("DIGEST_SITE_URL", "").strip()
    tg_chunks = render_telegram_chunks(edition, site_url=site_url)

    logger.info("Saved Markdown digest to %s", md_path)
    logger.info("Rebuilt GitHub Pages site at %s", index_html)
    logger.info(
        "Edition '%s': %d selected items across %d Telegram message(s)",
        edition_id,
        edition.total_selected,
        len(tg_chunks),
    )

    if dry_run or no_telegram:
        for i, chunk in enumerate(tg_chunks, start=1):
            print(f"\n--- TELEGRAM MESSAGE {i}/{len(tg_chunks)} ({len(chunk)} chars) ---")
            print(chunk)
    else:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not bot_token or not chat_id:
            logger.error(
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set to send Telegram messages "
                "(or pass --dry-run / --no-telegram)."
            )
            return 1
        send_telegram_messages(bot_token=bot_token, chat_id=chat_id, messages=tg_chunks)
        logger.info("Sent %d Telegram message(s) successfully.", len(tg_chunks))

    if not dry_run:
        dedup_store.mark_seen(selected_items, now=utc_now)
        dedup_store.save()

    return 0


def cmd_get_chat_id() -> int:
    """Print chat IDs from Telegram getUpdates."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Error: set TELEGRAM_BOT_TOKEN environment variable first.", file=sys.stderr)
        return 1
    chats = fetch_chat_ids(token)
    if not chats:
        print("No chats found. Send a message (e.g. /start) to your bot on Telegram and retry.")
        return 0
    for c in chats:
        print(f"Chat ID: {c['id']} | type={c['type']} | name={c['title']}")
    return 0


def cmd_rebuild_site() -> int:
    """Rebuild docs/ static site from data/digests_index.json."""
    out = build_site(index_path=INDEX_PATH, docs_dir=DOCS_DIR)
    print(f"Rebuilt site at {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Personal News & Trends Digest Engine")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Fetch sources, build digest/site, and deliver to Telegram")
    run_p.add_argument("--config", default=None, help="Optional path to custom config.yaml")
    run_p.add_argument("--dry-run", action="store_true", help="Print Telegram payload; do not send or update seen_items.json")
    run_p.add_argument("--no-telegram", action="store_true", help="Skip Telegram delivery but update seen_items.json")
    run_p.add_argument("--no-dedup", action="store_true", help="Ignore seen_items.json history for this run")
    run_p.add_argument("--edition", default="", help="Override edition slot (morning, afternoon, night)")

    sub.add_parser("get-chat-id", help="Discover your Telegram chat ID using TELEGRAM_BOT_TOKEN")
    sub.add_parser("rebuild-site", help="Rebuild docs/ static site from data/digests_index.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return run_digest(
            config_path=args.config,
            dry_run=args.dry_run,
            no_telegram=args.no_telegram,
            no_dedup=args.no_dedup,
            edition_override=args.edition,
        )
    if args.command == "get-chat-id":
        return cmd_get_chat_id()
    if args.command == "rebuild-site":
        return cmd_rebuild_site()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
