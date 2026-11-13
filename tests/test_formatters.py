"""Unit tests for Markdown, Telegram HTML escaping/chunking, and GitHub Pages site builder."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config_loader import load_config
from src.formatters.markdown_fmt import save_markdown_and_index
from src.formatters.site_builder import build_site
from src.formatters.telegram_fmt import render_telegram_chunks
from src.models import DigestEdition, NewsItem


def _sample_edition(items_per_cat: int = 2) -> DigestEdition:
    config = load_config()
    tz = timezone(timedelta(hours=4), name="UTC+04:00")
    now = datetime(2026, 11, 13, 8, 0, tzinfo=tz)
    items_by_cat: dict[str, list[NewsItem]] = {}

    for cat in config.categories:
        items_by_cat[cat.key] = [
            NewsItem(
                id=f"{cat.key}:{i}",
                title=f"Title <script> & Test {cat.key} #{i}",
                url=f"https://example.com/{cat.key}/{i}?a=1&b=2",
                source_name="Hacker News" if i % 2 == 0 else "GitHub Trending",
                source_type="hn" if i % 2 == 0 else "github",
                summary=f"Summary with <tags> & details for item {i} " + ("word " * 15),
                raw_score=100 + i,
                secondary_metric=25 + i,
                comments_url=f"https://news.ycombinator.com/item?id={1000 + i}" if i % 2 == 0 else "",
                assigned_category=cat.key,
            )
            for i in range(items_per_cat)
        ]

    return DigestEdition(
        edition_id="2026-11-13-0800",
        edition_key="morning",
        edition_label="Morning Update (08:00)",
        generated_at=now,
        categories=config.categories,
        items_by_category=items_by_cat,
        total_fetched=50,
        total_after_dedup=40,
    )


def test_telegram_escaping_and_chunking() -> None:
    edition = _sample_edition(items_per_cat=4)
    chunks = render_telegram_chunks(
        edition,
        site_url="https://user.github.io/news-update/",
        max_chars=1200,
    )
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 1200
        assert "<script>" not in chunk
        assert "&lt;script&gt;" in chunk or "Browse Full Archive" in chunk or "Morning Update" in chunk

    joined = "\n".join(chunks)
    assert "&lt;script&gt; &amp; Test" in joined
    assert "https://user.github.io/news-update/" in joined


def test_markdown_and_site_builder(tmp_path: Path) -> None:
    edition = _sample_edition(items_per_cat=1)
    digests_dir = tmp_path / "digests"
    index_path = tmp_path / "data" / "digests_index.json"
    docs_dir = tmp_path / "docs"

    md_path, rel_md, rel_html = save_markdown_and_index(
        edition=edition,
        digests_dir=digests_dir,
        index_path=index_path,
    )
    assert md_path.exists()
    assert rel_md == "digests/2026/11/2026-11-13-0800.md"
    assert rel_html == "digests/2026/11/2026-11-13-0800.html"

    md_text = md_path.read_text(encoding="utf-8")
    assert "Morning Update (08:00)" in md_text
    assert "🤖 AI Agents & Agentic Workflows" in md_text

    site_index = build_site(index_path=index_path, docs_dir=docs_dir)
    assert site_index.exists()
    assert (docs_dir / ".nojekyll").exists()
    assert (docs_dir / rel_html).exists()
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / ".nojekyll").exists()

    html_content = site_index.read_text(encoding="utf-8")
    assert "&lt;script&gt; &amp; Test" in html_content
    assert "<script> & Test" not in html_content

    root_html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert f'href="docs/{rel_html}"' in root_html
