"""Unit tests for URL normalization, fuzzy/cloned deduplication, and TTL pruning."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.dedup import DedupStore, normalize_title, normalize_url
from src.models import NewsItem


def test_normalize_url_strips_tracking_and_fragments() -> None:
    u1 = "https://www.Example.com/post/ai-agents/?utm_source=hn&utm_medium=web&ref=top#comments"
    u2 = "http://example.com/post/ai-agents"
    assert normalize_url(u1) == "https://example.com/post/ai-agents"
    assert normalize_url(u2) == "http://example.com/post/ai-agents"
    assert normalize_url("https://www.example.com/a/?b=1&utm_campaign=x") == "https://example.com/a?b=1"


def test_normalize_title_canonicalization() -> None:
    assert normalize_title("Show HN: My New AI-Agent CLI!") == "show hn my new ai agent cli"


def test_dedup_store_batch_persistence_and_prune(tmp_path: Path) -> None:
    store_path = tmp_path / "seen_items.json"
    store = DedupStore(store_path)

    a = NewsItem(id="1", title="Agentic Workflows 101", url="https://example.com/a?utm_source=x", source_name="HN", source_type="hn")
    b_dup_url = NewsItem(id="2", title="Different Title Same URL", url="https://www.example.com/a/", source_name="RSS", source_type="rss")
    c_dup_title = NewsItem(id="3", title="Agentic Workflows 101!", url="https://other.org/a", source_name="RSS", source_type="rss")
    d_unique = NewsItem(id="4", title="New Rust CLI", url="https://github.com/org/cli", source_name="GitHub", source_type="github")

    unseen = store.filter_unseen([a, b_dup_url, c_dup_title, d_unique])
    assert [x.id for x in unseen] == ["1", "4"]

    now = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
    store.mark_seen(unseen, now=now)
    store.save()

    reloaded = DedupStore(store_path)
    assert reloaded.is_seen(a)
    assert reloaded.is_seen(b_dup_url)
    assert reloaded.is_seen(c_dup_title)
    assert reloaded.filter_unseen([a, d_unique]) == []

    # Prune 20 days later with 14-day retention
    removed = reloaded.prune(retention_days=14, now=now + timedelta(days=20))
    assert removed == 4
    assert reloaded.seen == {}


def test_cloned_summary_and_fuzzy_title_dedup(tmp_path: Path) -> None:
    store = DedupStore(tmp_path / "seen.json")

    clone1 = NewsItem(
        id="gh:1",
        title="user1/Blender-AI-MCP-Studio",
        url="https://github.com/user1/Blender-AI-MCP-Studio",
        source_name="GitHub Search",
        source_type="github",
        summary="Blender AI MCP Studio is an AI assistant and built-in Model Context Protocol server for Blender that turns plain-language briefs into scenes.",
        raw_score=90,
    )
    clone2 = NewsItem(
        id="gh:2",
        title="user2/Blender-AI-MCP-Suite",
        url="https://github.com/user2/Blender-AI-MCP-Suite",
        source_name="GitHub Search",
        source_type="github",
        summary="Blender AI MCP Suite is an AI assistant and built-in Model Context Protocol server for Blender that turns plain-language briefs into scenes.",
        raw_score=88,
    )
    hn_outage = NewsItem(
        id="hn:1",
        title="Service degradation on Microsoft 365 cloud outage",
        url="https://status.cloud.microsoft/",
        source_name="Hacker News",
        source_type="hn",
        raw_score=870,
    )
    rss_outage = NewsItem(
        id="rss:1",
        title="Microsoft 365 cloud outage causes global service degradation",
        url="https://techcrunch.com/microsoft-outage",
        source_name="TechCrunch",
        source_type="rss",
        summary="Users across regions reported outages.",
        raw_score=0,
    )

    unseen = store.filter_unseen([clone1, clone2, hn_outage, rss_outage])
    ids = [x.id for x in unseen]
    assert "gh:1" in ids
    assert "gh:2" not in ids
    assert "hn:1" in ids
    assert "rss:1" not in ids
    surviving_hn = next(x for x in unseen if x.id == "hn:1")
    assert surviving_hn.cross_source_hits == 2
    assert surviving_hn.summary == "Users across regions reported outages."
