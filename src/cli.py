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


TOPIC_ALIASES: dict[str, str] = {
    "ds_edu": "ds_edu",
    "guides": "ds_edu",
    "learn": "ds_edu",
    "stats": "ds_edu",
    "statistics": "ds_edu",
    "case_study": "ds_edu",
    "hn": "hackernews_top",
    "hackernews": "hackernews_top",
    "hackernews_top": "hackernews_top",
    "tc": "techcrunch_top",
    "techcrunch": "techcrunch_top",
    "techcrunch_top": "techcrunch_top",
    "tech": "trending_tech",
    "trending_tech": "trending_tech",
    "data": "data_science",
    "ds": "data_science",
    "analytics": "data_science",
    "bi": "data_science",
    "data_science": "data_science",
    "tools": "new_tools",
    "new_tools": "new_tools",
    "harnesses": "new_tools",
    "agents": "ai_agents",
    "ai_agents": "ai_agents",
    "agentic": "ai_agents",
    "skills": "ai_agents",
    "mcp": "ai_agents",
    "llm": "llm_models",
    "llms": "llm_models",
    "llm_models": "llm_models",
    "ai": "llm_models",
    "economy": "economics",
    "economics": "economics",
    "business": "economics",
    "markets": "economics",
    "finance": "economics",
    "case_studies": "economics",
    "politics": "trending_politics",
    "world": "trending_politics",
    "trending_politics": "trending_politics",
}

GITHUB_TOPIC_KEYS = frozenset({"trending_tech", "data_science", "new_tools", "ai_agents", "llm_models"})
HN_TOPIC_KEYS = GITHUB_TOPIC_KEYS | frozenset({"hackernews_top", "economics", "trending_politics"})


def resolve_topic_keys(topics_arg: str, config: AppConfig) -> list[str]:
    """Parse a comma/space-separated topic list into valid ordered category keys."""
    all_keys = [cat.key for cat in config.categories]
    scheduled_keys = [cat.key for cat in config.categories if not cat.on_demand_only]
    on_demand_keys = [cat.key for cat in config.categories if cat.on_demand_only]
    raw = (topics_arg or "").strip()
    if not raw or raw.lower() in {"scheduled", "default", "morning"}:
        return scheduled_keys
    if raw.lower() == "topics":
        return on_demand_keys
    if raw.lower() == "all":
        return all_keys

    requested: set[str] = set()
    for token in raw.replace(",", " ").split():
        cleaned = token.strip().lower().lstrip("/")
        if not cleaned:
            continue
        if cleaned == "all":
            return all_keys
        if cleaned == "topics":
            requested.update(on_demand_keys)
            continue
        if cleaned in {"scheduled", "default", "morning"}:
            requested.update(scheduled_keys)
            continue
        mapped = TOPIC_ALIASES.get(cleaned)
        if mapped and mapped in all_keys:
            requested.add(mapped)
        elif cleaned in all_keys:
            requested.add(cleaned)
        else:
            logger.warning("Ignoring unknown topic '%s'", token)

    if not requested:
        raise ValueError(
            f"No valid topics matched '{topics_arg}'. Valid keys: {', '.join(all_keys)}"
        )
    return [key for key in all_keys if key in requested]


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
    topics: str = "",
    chat_id_override: str = "",
    on_demand: bool = False,
) -> int:
    """Execute the fetch -> dedup -> classify -> save/site -> Telegram pipeline."""
    config: AppConfig = load_config(config_path)
    has_topics = bool(topics.strip())
    is_on_demand = on_demand or has_topics
    active_topic_keys = (
        resolve_topic_keys(topics, config)
        if has_topics
        else [cat.key for cat in config.categories if not cat.on_demand_only]
    )
    lookback_hours = (
        config.on_demand_lookback_hours
        if is_on_demand
        else config.scheduled_lookback_hours
    )

    tz = timezone(timedelta(hours=config.timezone_offset_hours), name=f"UTC+{config.timezone_offset_hours:02d}:00")
    utc_now = datetime.now(timezone.utc)
    local_now = utc_now.astimezone(tz)

    if is_on_demand:
        edition_key = "on_demand"
        edition_label = "On-Demand Topic Brief"
    else:
        edition_key, edition_label = resolve_edition_slot(
            local_now=local_now,
            schedule_slots=config.schedule_slots,
            override=edition_override,
        )
    edition_id = local_now.strftime("%Y-%m-%d-%H%M")

    # Fetch only the sources required by active categories
    active_key_set = set(active_topic_keys)
    active_categories = [c for c in config.categories if c.key in active_key_set]
    pinned_source_names = {
        name for cat in active_categories for name in cat.source_names
    }
    active_feeds = [
        feed
        for feed in config.rss_feeds
        if feed.default_category in active_key_set or feed.name in pinned_source_names
    ]

    fetched: list[NewsItem] = []
    if active_key_set & HN_TOPIC_KEYS:
        fetched.extend(
            fetch_hackernews(
                config.hackernews,
                max_summary_chars=config.digest.description_max_chars,
                lookback_hours=lookback_hours,
                include_targeted=bool(active_key_set & GITHUB_TOPIC_KEYS),
                now=utc_now,
            )
        )
    if active_key_set & GITHUB_TOPIC_KEYS:
        fetched.extend(
            fetch_github(
                config.github,
                max_summary_chars=config.digest.description_max_chars,
                now=utc_now,
            )
        )
    if active_feeds:
        fetched.extend(
            fetch_rss_feeds(
                active_feeds,
                max_summary_chars=config.digest.description_max_chars,
                lookback_hours=lookback_hours,
                now=utc_now,
            )
        )

    dedup_store = DedupStore(SEEN_PATH)
    dedup_store.prune(config.digest.dedup_retention_days, now=utc_now)

    # On-demand briefs ignore seen_items.json history so they always return the latest 24h stories
    unseen = dedup_store.filter_unseen(fetched, ignore_history=(no_dedup or is_on_demand))

    classifier = NewsClassifier(config.categories)
    items_by_category = classifier.classify_and_select(
        unseen, config=config, now=utc_now, active_keys=active_key_set
    )

    items_by_category = {k: items_by_category.get(k, []) for k in active_topic_keys}

    selected_items = [
        item
        for cat in active_categories
        for item in items_by_category.get(cat.key, [])
    ]
    enrich_missing_summaries(selected_items, max_chars=config.digest.description_max_chars)

    edition = DigestEdition(
        edition_id=edition_id,
        edition_key=edition_key,
        edition_label=edition_label,
        generated_at=local_now,
        categories=active_categories,
        items_by_category=items_by_category,
        total_fetched=len(fetched),
        total_after_dedup=len(unseen),
    )

    if not is_on_demand and not dry_run:
        md_path, _, _ = save_markdown_and_index(
            edition=edition,
            digests_dir=DIGESTS_DIR,
            index_path=INDEX_PATH,
        )
        index_html = build_site(index_path=INDEX_PATH, docs_dir=DOCS_DIR)
        logger.info("Saved Markdown digest to %s", md_path)
        logger.info("Rebuilt GitHub Pages site at %s", index_html)

    site_url = os.getenv("DIGEST_SITE_URL", "").strip()
    tg_chunks = render_telegram_chunks(edition, site_url=site_url)

    logger.info(
        "Edition '%s' (%s): %d selected items across %d Telegram message(s)",
        edition_id,
        edition_key,
        edition.total_selected,
        len(tg_chunks),
    )

    if dry_run or no_telegram:
        for i, chunk in enumerate(tg_chunks, start=1):
            print(f"\n--- TELEGRAM MESSAGE {i}/{len(tg_chunks)} ({len(chunk)} chars) ---")
            print(chunk)
    else:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = chat_id_override.strip() or os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not bot_token or not chat_id:
            logger.error(
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set to send Telegram messages "
                "(or pass --dry-run / --no-telegram)."
            )
            return 1
        send_telegram_messages(bot_token=bot_token, chat_id=chat_id, messages=tg_chunks)
        logger.info("Sent %d Telegram message(s) successfully.", len(tg_chunks))

    if not dry_run and not is_on_demand:
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
    run_p.add_argument("--topics", default="", help="Comma-separated topics/aliases (e.g. politics,economy)")
    run_p.add_argument("--chat-id", default="", help="Override TELEGRAM_CHAT_ID for delivery")
    run_p.add_argument("--on-demand", action="store_true", help="Ephemeral on-demand brief (skip archive & dedup write)")

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
            topics=args.topics,
            chat_id_override=args.chat_id,
            on_demand=args.on_demand,
        )
    if args.command == "get-chat-id":
        return cmd_get_chat_id()
    if args.command == "rebuild-site":
        return cmd_rebuild_site()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
