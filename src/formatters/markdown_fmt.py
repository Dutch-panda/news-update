"""Markdown digest renderer and digests_index.json updater."""

from __future__ import annotations

import json
from pathlib import Path

from src.formatters import format_item_metrics
from src.models import DigestEdition


def render_markdown(edition: DigestEdition) -> str:
    """Render a DigestEdition as a clean Markdown document."""
    date_str = edition.generated_at.strftime("%Y-%m-%d %H:%M %Z")
    lines: list[str] = [
        f"# {edition.edition_label} — {edition.generated_at.strftime('%Y-%m-%d')}",
        "",
        f"- **Generated:** {date_str}",
        f"- **Selected Items:** {edition.total_selected} (from {edition.total_after_dedup} unseen / {edition.total_fetched} fetched)",
        "",
    ]

    for cat in edition.categories:
        items = edition.items_by_category.get(cat.key, [])
        if not items:
            continue
        lines.append(f"## {cat.title}")
        lines.append("")
        for idx, item in enumerate(items, start=1):
            metrics = format_item_metrics(item)
            meta_parts = [item.source_name]
            if metrics:
                meta_parts.append(metrics)
            if item.comments_url and item.comments_url != item.url:
                meta_parts.append(f"[HN Discussion]({item.comments_url})")

            meta_line = " · ".join(meta_parts)
            lines.append(f"{idx}. **[{item.title}]({item.url})**  ")
            lines.append(f"   _{meta_line}_")
            if item.summary:
                lines.append(f"   > {item.summary}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def save_markdown_and_index(
    edition: DigestEdition,
    digests_dir: Path | str,
    index_path: Path | str,
) -> tuple[Path, str, str]:
    """Save Markdown digest file and upsert its entry into data/digests_index.json."""
    digests_root = Path(digests_dir)
    year = edition.generated_at.strftime("%Y")
    month = edition.generated_at.strftime("%m")
    filename = f"{edition.edition_id}.md"

    rel_md_path = f"digests/{year}/{month}/{filename}"
    rel_html_path = f"digests/{year}/{month}/{edition.edition_id}.html"
    target_path = digests_root / year / month / filename
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(render_markdown(edition), encoding="utf-8")

    idx_file = Path(index_path)
    idx_file.parent.mkdir(parents=True, exist_ok=True)
    if idx_file.exists():
        data = json.loads(idx_file.read_text(encoding="utf-8"))
        entries = [e for e in data.get("digests", []) if e.get("edition_id") != edition.edition_id]
    else:
        entries = []

    entries.append(edition.to_index_entry(markdown_rel_path=rel_md_path, html_rel_path=rel_html_path))
    entries.sort(key=lambda e: e.get("generated_at", ""), reverse=True)

    idx_file.write_text(
        json.dumps({"digests": entries}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target_path, rel_md_path, rel_html_path
