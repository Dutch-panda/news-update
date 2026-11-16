"""Unit tests for keyword classification, ordering, specificity, scoring, and diversity."""

from datetime import datetime, timedelta, timezone

from src.classifier import NewsClassifier
from src.cli import resolve_topic_keys
from src.config_loader import load_config
from src.models import NewsItem


def test_category_display_order_limits_and_lookbacks() -> None:
    config = load_config()
    assert [c.key for c in config.categories] == [
        "trending_tech",
        "data_science",
        "new_tools",
        "ai_agents",
        "llm_models",
        "economics",
        "trending_politics",
    ]
    assert [cat.max_items for cat in config.categories] == [15, 15, 15, 15, 15, 10, 10]
    assert [cat.on_demand_only for cat in config.categories] == [
        False,
        False,
        False,
        False,
        False,
        True,
        True,
    ]
    assert config.scheduled_lookback_hours == 10
    assert config.on_demand_lookback_hours == 24
    assert config.github.search_enabled is False


def test_data_science_economics_and_topic_aliases() -> None:
    config = load_config()
    classifier = NewsClassifier(config.categories)

    ds_item = NewsItem(
        id="rss:duckdb:1",
        title="Fast local analytics and SQL warehousing with DuckDB and Polars",
        url="https://duckdb.org/post/1",
        source_name="DuckDB Blog",
        source_type="rss",
        default_category="data_science",
    )
    assert classifier.score_item(ds_item).assigned_category == "data_science"

    econ_item = NewsItem(
        id="rss:mit:1",
        title="Case study on supply chain productivity and central bank interest rate impact",
        url="https://sloanreview.mit.edu/case-1",
        source_name="MIT Sloan Management Review",
        source_type="rss",
        default_category="economics",
    )
    assert classifier.score_item(econ_item).assigned_category == "economics"

    assert resolve_topic_keys("scheduled", config) == [
        "trending_tech",
        "data_science",
        "new_tools",
        "ai_agents",
        "llm_models",
    ]
    assert resolve_topic_keys("politics, economy", config) == [
        "economics",
        "trending_politics",
    ]
    assert resolve_topic_keys("data agents llm", config) == [
        "data_science",
        "ai_agents",
        "llm_models",
    ]
    assert resolve_topic_keys("all", config) == [c.key for c in config.categories]


def test_word_boundaries_prevent_false_positives() -> None:
    config = load_config()
    classifier = NewsClassifier(config.categories)

    # "said" / "claim" / "detail" contain "ai" as a substring, should NOT match ai_agents/llm_models
    item = NewsItem(
        id="rss:bbc:1",
        title="Minister said new trade claim lacks detail",
        url="https://example.com/world/trade",
        source_name="BBC World",
        source_type="rss",
        summary="Parliament debated the treaty.",
        default_category="trending_politics",
    )
    scored = classifier.score_item(item)
    assert scored.assigned_category == "trending_politics"
    assert "ai" not in scored.matched_keywords


def test_specificity_tie_break_independent_of_display_order() -> None:
    config = load_config()
    classifier = NewsClassifier(config.categories)

    # Exact 1-keyword tie: "Google" (trending_tech) vs "Claude" (llm_models) -> llm_models wins tie
    item = NewsItem(
        id="hn:200",
        title="Google compares Claude in evaluation",
        url="https://example.com/compare",
        source_name="Hacker News",
        source_type="hn",
        raw_score=100,
    )
    scored = classifier.score_item(item)
    assert scored.assigned_category == "llm_models"


def test_github_relevance_filter_and_category_classification() -> None:
    config = load_config()
    classifier = NewsClassifier(config.categories)

    agent_item = NewsItem(
        id="hn:100",
        title="Building reliable multi-agent workflows with MCP and tool calling",
        url="https://example.com/agents",
        source_name="Hacker News",
        source_type="hn",
        raw_score=320,
        secondary_metric=85,
    )
    llm_item = NewsItem(
        id="hn:101",
        title="New GPT-5 reasoning model weights benchmarked with vLLM",
        url="https://example.com/llm",
        source_name="Hacker News",
        source_type="hn",
        raw_score=410,
        secondary_metric=120,
    )
    gh_tool = NewsItem(
        id="gh:acme/fast-cli",
        title="acme/fast-cli",
        url="https://github.com/acme/fast-cli",
        source_name="GitHub Trending",
        source_type="github",
        summary="[Rust] A blazing fast self-hosted terminal CLI tool",
        raw_score=1500,
        secondary_metric=340,
    )
    gh_irrelevant_low = NewsItem(
        id="gh:other/wallpaper-pack",
        title="other/wallpaper-pack",
        url="https://github.com/other/wallpaper-pack",
        source_name="GitHub Trending",
        source_type="github",
        summary="Cute desktop wallpapers collection",
        raw_score=200,
        secondary_metric=25,
    )

    assert classifier.score_item(agent_item).assigned_category == "ai_agents"
    assert classifier.score_item(llm_item).assigned_category == "llm_models"
    assert classifier.score_item(gh_tool).assigned_category == "new_tools"
    assert classifier.score_item(gh_irrelevant_low).assigned_category == ""


def test_trending_counts_and_source_diversity() -> None:
    config = load_config()
    classifier = NewsClassifier(config.categories)
    now = datetime.now(timezone.utc)

    items: list[NewsItem] = []
    # 9 from BBC + 9 from Guardian + 3 from NPR -> capped at 7 per source, 10 total in trending_politics
    for i in range(9):
        items.append(
            NewsItem(
                id=f"rss:bbc:{i}",
                title=f"Election and parliament update {i}",
                url=f"https://bbc.co.uk/news/{i}",
                source_name="BBC World",
                source_type="rss",
                published_at=now - timedelta(hours=i),
                default_category="trending_politics",
            )
        )
    for i in range(9):
        items.append(
            NewsItem(
                id=f"rss:guardian:{i}",
                title=f"Senate votes on new sanctions legislation {i}",
                url=f"https://theguardian.com/world/{i}",
                source_name="The Guardian World",
                source_type="rss",
                published_at=now - timedelta(hours=i),
                default_category="trending_politics",
            )
        )
    # 9 from HN + 9 from TechCrunch + 3 from Ars Technica -> capped at 7 per source, 15 total in trending_tech
    for i in range(9):
        items.append(
            NewsItem(
                id=f"hn:tech:{i}",
                title=f"Semiconductor hardware chip update {i}",
                url=f"https://tech.example.com/hn/{i}",
                source_name="Hacker News",
                source_type="hn",
                raw_score=200 - i * 10,
                published_at=now - timedelta(hours=i),
                default_category="trending_tech",
            )
        )
    for i in range(9):
        items.append(
            NewsItem(
                id=f"rss:tc:{i}",
                title=f"Nvidia startup funding hardware {i}",
                url=f"https://techcrunch.com/news/{i}",
                source_name="TechCrunch",
                source_type="rss",
                published_at=now - timedelta(hours=i),
                default_category="trending_tech",
            )
        )
    for i in range(3):
        items.append(
            NewsItem(
                id=f"rss:ars:{i}",
                title=f"Linux cloud vulnerability update {i}",
                url=f"https://arstechnica.com/news/{i}",
                source_name="Ars Technica",
                source_type="rss",
                published_at=now - timedelta(hours=i),
                default_category="trending_tech",
            )
        )

    selected = classifier.classify_and_select(items, config=config, now=now)
    assert len(selected["trending_tech"]) == 15
    assert len(selected["trending_politics"]) == 10
    bbc_count = sum(1 for x in selected["trending_politics"] if x.source_name == "BBC World")
    assert bbc_count == config.digest.max_per_source_in_category
    assert bbc_count == 7


def test_arxiv_combined_cap_per_category() -> None:
    config = load_config()
    classifier = NewsClassifier(config.categories)
    now = datetime.now(timezone.utc)

    items = [
        NewsItem(
            id=f"rss:arxiv:{i}",
            title=f"LLM reasoning transformer benchmark paper {i}",
            url=f"https://arxiv.org/abs/2611.{1000 + i}",
            source_name="ArXiv cs.AI" if i % 2 == 0 else "ArXiv cs.LG",
            source_type="rss",
            published_at=now - timedelta(hours=1),
            default_category="llm_models",
        )
        for i in range(6)
    ]
    items.append(
        NewsItem(
            id="rss:simon:1",
            title="New Claude reasoning model test",
            url="https://simonwillison.net/test",
            source_name="Simon Willison",
            source_type="rss",
            published_at=now - timedelta(hours=1),
            default_category="llm_models",
        )
    )

    selected = classifier.classify_and_select(items, config=config, now=now)
    arxiv_items = [x for x in selected["llm_models"] if x.source_name.startswith("ArXiv")]
    assert len(arxiv_items) == 3
    assert any(x.source_name == "Simon Willison" for x in selected["llm_models"])
