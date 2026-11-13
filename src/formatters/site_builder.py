"""Static GitHub Pages site builder reading from data/digests_index.json."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

SITE_CSS = """
:root {
  --bg: #0f172a;
  --surface: #1e293b;
  --surface-hover: #263348;
  --border: #334155;
  --text: #f1f5f9;
  --muted: #94a3b8;
  --accent: #38bdf8;
  --pill-bg: #0ea5e922;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f8fafc;
    --surface: #ffffff;
    --surface-hover: #f1f5f9;
    --border: #e2e8f0;
    --text: #0f172a;
    --muted: #475569;
    --accent: #0284c7;
    --pill-bg: #e0f2fe;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.55;
}
.container {
  max-width: 1080px;
  margin: 0 auto;
  padding: 1.5rem 1rem 3rem;
}
header {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: baseline;
  gap: 0.75rem;
  border-bottom: 1px solid var(--border);
  padding-bottom: 1rem;
  margin-bottom: 1.5rem;
}
header h1 { margin: 0; font-size: 1.5rem; }
header a { color: var(--accent); text-decoration: none; }
.meta { color: var(--muted); font-size: 0.9rem; }
.layout {
  display: grid;
  grid-template-columns: 1fr 270px;
  gap: 1.5rem;
}
@media (max-width: 820px) {
  .layout { grid-template-columns: 1fr; }
}
.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 1.25rem;
}
.controls input[type="search"] {
  flex: 1 1 220px;
  padding: 0.5rem 0.75rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  font-size: 0.95rem;
}
.pill {
  padding: 0.35rem 0.7rem;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  cursor: pointer;
  font-size: 0.85rem;
}
.pill.active {
  background: var(--pill-bg);
  border-color: var(--accent);
  color: var(--accent);
  font-weight: 600;
}
.category-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1rem 1.25rem;
  margin-bottom: 1.25rem;
}
.category-card h2 {
  margin: 0 0 0.85rem;
  font-size: 1.1rem;
  border-bottom: 1px solid var(--border);
  padding-bottom: 0.45rem;
}
.item {
  padding: 0.65rem 0;
  border-bottom: 1px solid var(--border);
}
.item:last-child { border-bottom: none; padding-bottom: 0; }
.item-title {
  color: var(--accent);
  text-decoration: none;
  font-weight: 600;
}
.item-title:hover { text-decoration: underline; }
.item-meta {
  font-size: 0.82rem;
  color: var(--muted);
  margin-top: 0.15rem;
}
.item-meta a { color: var(--accent); text-decoration: none; }
.item-summary {
  margin: 0.3rem 0 0;
  font-size: 0.92rem;
}
aside .archive-box {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1rem;
}
aside h3 { margin: 0 0 0.75rem; font-size: 1rem; }
aside ul { list-style: none; padding: 0; margin: 0; }
aside li { margin-bottom: 0.55rem; font-size: 0.88rem; }
aside a { color: var(--accent); text-decoration: none; }
aside a.current { font-weight: 700; text-decoration: underline; }
"""

FILTER_SCRIPT = """
<script>
(function() {
  const searchInput = document.getElementById('search-input');
  const pills = document.querySelectorAll('.pill[data-cat]');
  let activeCat = 'all';

  function applyFilter() {
    const query = (searchInput ? searchInput.value : '').trim().toLowerCase();
    document.querySelectorAll('.category-card').forEach(card => {
      const cat = card.getAttribute('data-cat');
      const catMatch = (activeCat === 'all' || cat === activeCat);
      let visibleItems = 0;
      card.querySelectorAll('.item').forEach(item => {
        const text = item.textContent.toLowerCase();
        const show = catMatch && (!query || text.includes(query));
        item.style.display = show ? '' : 'none';
        if (show) visibleItems++;
      });
      card.style.display = visibleItems > 0 ? '' : 'none';
    });
  }

  pills.forEach(btn => {
    btn.addEventListener('click', () => {
      pills.forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      activeCat = btn.getAttribute('data-cat');
      applyFilter();
    });
  });

  if (searchInput) {
    searchInput.addEventListener('input', applyFilter);
  }
})();
</script>
"""


def _format_entry_metrics(item: dict[str, Any]) -> str:
    source_type = item.get("source_type", "")
    raw_score = int(item.get("raw_score") or 0)
    sec = int(item.get("secondary_metric") or 0)
    if source_type == "github":
        stars = f"⭐ {raw_score:,}"
        if sec > 0 and item.get("source_name") == "GitHub Trending":
            return f"{stars} (+{sec:,} today)"
        return stars
    if source_type == "hn":
        return f"▲ {raw_score:,} pts · 💬 {sec:,}"
    return ""


def _render_digest_page(
    entry: dict[str, Any] | None,
    all_entries: list[dict[str, Any]],
    archive_prefix: str,
    home_href: str,
    current_id: str,
) -> str:
    if entry is None:
        main_html = '<div class="category-card"><p>No digests generated yet.</p></div>'
        subtitle = "Personal News &amp; Trends Archive"
        pills_html = ""
    else:
        subtitle = (
            f"{html.escape(entry.get('edition_label', 'Digest'))} · "
            f"{html.escape(entry.get('generated_at', '')[:16].replace('T', ' '))}"
        )
        categories = entry.get("categories", [])
        pills = ['<button class="pill active" data-cat="all">All</button>']
        cards: list[str] = []

        for cat in categories:
            cat_key = html.escape(cat.get("key", ""), quote=True)
            cat_title = html.escape(cat.get("title", ""))
            pills.append(f'<button class="pill" data-cat="{cat_key}">{cat_title}</button>')

            items_html: list[str] = []
            for item in cat.get("items", []):
                title = html.escape(item.get("title", ""))
                url = html.escape(item.get("url", ""), quote=True)
                source = html.escape(item.get("source_name", ""))
                metrics = html.escape(_format_entry_metrics(item))
                comments_url = item.get("comments_url") or ""
                summary = html.escape(item.get("summary", ""))

                meta_parts = [source]
                if metrics:
                    meta_parts.append(metrics)
                if comments_url and comments_url != item.get("url"):
                    meta_parts.append(
                        f'<a href="{html.escape(comments_url, quote=True)}" target="_blank" rel="noopener">HN Discussion</a>'
                    )

                summary_p = f'<p class="item-summary">{summary}</p>' if summary else ""
                items_html.append(
                    f'<div class="item">'
                    f'<a class="item-title" href="{url}" target="_blank" rel="noopener">{title}</a>'
                    f'<div class="item-meta">{" · ".join(meta_parts)}</div>'
                    f"{summary_p}"
                    f"</div>"
                )

            cards.append(
                f'<section class="category-card" data-cat="{cat_key}">'
                f"<h2>{cat_title}</h2>"
                f'{"".join(items_html)}'
                f"</section>"
            )

        pills_html = (
            '<div class="controls">'
            '<input id="search-input" type="search" placeholder="Filter stories, repos, or keywords…">'
            f'{"".join(pills)}'
            "</div>"
        )
        main_html = "".join(cards) if cards else '<div class="category-card"><p>No items in this edition.</p></div>'

    archive_links: list[str] = []
    for e in all_entries[:40]:
        eid = e.get("edition_id", "")
        rel = e.get("html_path", "")
        href = f"{archive_prefix}{rel}"
        label = f"{eid[:10]} · {e.get('edition_label', eid)}"
        cls = ' class="current"' if eid == current_id else ""
        archive_links.append(
            f'<li><a{cls} href="{html.escape(href, quote=True)}">{html.escape(label)}</a></li>'
        )
    archive_html = "".join(archive_links) or "<li>No past digests yet.</li>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>News &amp; Trends Digest</title>
  <style>{SITE_CSS}</style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1><a href="{html.escape(home_href, quote=True)}">📡 News &amp; Trends Digest</a></h1>
        <div class="meta">{subtitle}</div>
      </div>
      <div class="meta">Hacker News · GitHub · Tech &amp; World RSS</div>
    </header>
    <div class="layout">
      <main>
        {pills_html}
        {main_html}
      </main>
      <aside>
        <div class="archive-box">
          <h3>Past Editions</h3>
          <ul>{archive_html}</ul>
        </div>
      </aside>
    </div>
  </div>
  {FILTER_SCRIPT}
</body>
</html>
"""


def build_site(index_path: Path | str, docs_dir: Path | str) -> Path:
    """Rebuild the static HTML archive in docs/ and root index.html from data/digests_index.json."""
    idx_file = Path(index_path)
    docs_root = Path(docs_dir)
    repo_root = docs_root.parent
    docs_root.mkdir(parents=True, exist_ok=True)
    (docs_root / ".nojekyll").write_text("", encoding="utf-8")
    (repo_root / ".nojekyll").write_text("", encoding="utf-8")

    entries: list[dict[str, Any]] = []
    if idx_file.exists():
        data = json.loads(idx_file.read_text(encoding="utf-8"))
        entries = list(data.get("digests", []))

    latest = entries[0] if entries else None
    latest_id = latest.get("edition_id", "") if latest else ""

    docs_index_html = _render_digest_page(
        entry=latest,
        all_entries=entries,
        archive_prefix="",
        home_href="index.html",
        current_id=latest_id,
    )
    index_out = docs_root / "index.html"
    index_out.write_text(docs_index_html, encoding="utf-8")

    # Also write index.html at repository root so GitHub Pages works with either / (root) or /docs
    root_index_html = _render_digest_page(
        entry=latest,
        all_entries=entries,
        archive_prefix=f"{docs_root.name}/",
        home_href="index.html",
        current_id=latest_id,
    )
    (repo_root / "index.html").write_text(root_index_html, encoding="utf-8")

    for entry in entries:
        rel_html = entry.get("html_path", "")
        if not rel_html:
            continue
        depth = len(Path(rel_html).parts) - 1
        prefix = "../" * depth
        page_html = _render_digest_page(
            entry=entry,
            all_entries=entries,
            archive_prefix=prefix,
            home_href=f"{prefix}index.html",
            current_id=entry.get("edition_id", ""),
        )
        out_path = docs_root / rel_html
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(page_html, encoding="utf-8")

    return index_out
