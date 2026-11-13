"""Unit tests for keyword classification, ordering, specificity, scoring, and diversity."""

from datetime import datetime, timedelta, timezone

from src.classifier import NewsClassifier
from src.cli import resolve_topic_keys
from src.config_loader import load_config
from src.models import NewsItem


def test_category_display_order_and_limits() -> None:
    config = load_config()
    assert [c.key for c in config.categories] == [
        "trending_tech",
        "trending_politics",
        "economics",
        "new_tools",
        "ai_agents",
        "llm_models",
    ]
    assert [cat.max_items for cat in config.categories] == [15, 10, 10, 15, 15, 15]


def test_economics_classification_and_topic_aliases() -> None:
    config = load_config()
    classifier = NewsClassifier(config.categories)

    econ_item = NewsItem(
        id="rss:cnbc:1",
        title="Federal Reserve signals pause on interest rate cuts amid inflation data",
        url="https://example.com/fed-rates",
        source_name="CNBC Economy",
        source_type="rss",
        default_category="economics",
    )
    assert classifier.score_item(econ_item).assigned_category == "economics"

    assert resolve_topic_keys("politics, economy", config) == [
        "trending_politics",
        "economics",
    ]
    assert resolve_topic_keys("agents llm", config) == [
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


def test_prefix_and_category_classification() -> None:
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

    assert classifier.score_item(agent_item).assigned_category == "ai_agents"
    assert classifier.score_item(llm_item).assigned_category == "llm_models"
    assert classifier.score_item(gh_tool).assigned_category == "new_tools"


def test_trending_counts_reach_ten_and_enforce_diversity() -> None:
    config = load_config()
    classifier = NewsClassifier(config.categories)
    now = datetime.now(timezone.utc)

    items: list[NewsItem] = []
    # 9 from BBC + 9 from Guardian + 3 from NYT -> capped at 7 per source, 15 total in trending_politics
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
    for i in range(3):
        items.append(
            NewsItem(
                id=f"rss:nyt:{i}",
                title=f"Treaty and diplomacy talks {i}",
                url=f"https://nytimes.com/world/{i}",
                source_name="NYT World",
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
