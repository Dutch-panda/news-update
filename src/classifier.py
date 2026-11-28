"""Rule-based keyword classification and composite popularity/relevance ranking."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime, timezone

from src.models import AppConfig, CategoryConfig, NewsItem

TITLE_MATCH_WEIGHT = 3.0
SUMMARY_MATCH_WEIGHT = 1.2
MAX_RELEVANCE_SCORE = 6.0
GITHUB_TOOLS_PRIOR = 2.0
SHOW_HN_TOOLS_PRIOR = 2.0
CROSS_SOURCE_BONUS = 1.5
MAX_ARXIV_PER_CATEGORY = 3

# Tie-break priority independent of display order: specialized categories win ties over broad news
SPECIFICITY_ORDER = (
    "ai_agents",
    "llm_models",
    "data_science",
    "new_tools",
    "economics",
    "trending_politics",
    "trending_tech",
)


def _compile_keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Compile a regex for a keyword using word boundaries for alphanumeric tokens."""
    kw = keyword.strip().lower()
    escaped = re.escape(kw)
    if kw.endswith("-"):
        # Prefix like "gpt-" should match gpt-4, gpt-5, etc.
        return re.compile(rf"\b{escaped}", re.IGNORECASE)
    if re.match(r"^[a-z0-9]", kw) and re.match(r".*[a-z0-9]$", kw):
        return re.compile(rf"\b{escaped}\b", re.IGNORECASE)
    return re.compile(escaped, re.IGNORECASE)


class NewsClassifier:
    """Categorizes and ranks NewsItem objects without an LLM."""

    def __init__(self, categories: list[CategoryConfig]) -> None:
        self.categories = categories
        self.topic_categories = [
            cat for cat in categories if not cat.source_types and not cat.source_names
        ]
        self._patterns: dict[str, list[tuple[str, re.Pattern[str]]]] = {
            cat.key: [(kw, _compile_keyword_pattern(kw)) for kw in cat.keywords]
            for cat in categories
        }
        self._topic_keys = {cat.key for cat in self.topic_categories}
        self._specificity_rank = {key: idx for idx, key in enumerate(SPECIFICITY_ORDER)}

    def _match_category_keywords(
        self, item: NewsItem, cat: CategoryConfig
    ) -> tuple[float, list[str]]:
        title = item.title or ""
        summary = item.summary or ""
        cat_score = 0.0
        cat_matches: list[str] = []
        for kw, pattern in self._patterns.get(cat.key, []):
            in_title = bool(pattern.search(title))
            in_summary = bool(pattern.search(summary))
            if in_title or in_summary:
                cat_matches.append(kw)
                if in_title:
                    cat_score += TITLE_MATCH_WEIGHT
                if in_summary:
                    cat_score += SUMMARY_MATCH_WEIGHT
        return cat_score, cat_matches

    def score_item(self, item: NewsItem, now: datetime | None = None) -> NewsItem:
        """Assign topic category, relevance score, popularity score, and final score to an item."""
        if not self.topic_categories:
            item.popularity_score = self._compute_popularity(item, now=now)
            item.final_score = item.popularity_score
            return item

        title = item.title or ""
        scores_by_cat: dict[str, float] = {}
        matched_by_cat: dict[str, list[str]] = {}

        for cat in self.topic_categories:
            cat_score, cat_matches = self._match_category_keywords(item, cat)
            if cat_score > 0:
                scores_by_cat[cat.key] = cat_score
                matched_by_cat[cat.key] = cat_matches

        has_keyword_match = bool(scores_by_cat)
        if item.source_type == "github" and not has_keyword_match and item.secondary_metric < 100:
            item.assigned_category = ""
            item.relevance_score = 0.0
            item.popularity_score = 0.0
            item.final_score = 0.0
            item.matched_keywords = []
            return item

        # Structural priors for developer tools / repos when they don't strongly match AI/LLM
        if "new_tools" in self._topic_keys:
            if item.source_type == "github":
                scores_by_cat["new_tools"] = scores_by_cat.get("new_tools", 0.0) + GITHUB_TOOLS_PRIOR
            elif title.lower().startswith("show hn:"):
                scores_by_cat["new_tools"] = scores_by_cat.get("new_tools", 0.0) + SHOW_HN_TOOLS_PRIOR

        if scores_by_cat:
            best_cat = max(
                self.topic_categories,
                key=lambda c: (
                    scores_by_cat.get(c.key, -1.0),
                    -self._specificity_rank.get(c.key, 999),
                ),
            )
            chosen_key = best_cat.key
            item.relevance_score = min(MAX_RELEVANCE_SCORE, scores_by_cat[chosen_key])
            item.matched_keywords = matched_by_cat.get(chosen_key, [])
        else:
            chosen_key = (
                item.default_category
                if item.default_category in self._topic_keys
                else self.topic_categories[0].key
            )
            item.relevance_score = 0.5
            item.matched_keywords = []

        item.assigned_category = chosen_key
        item.popularity_score = self._compute_popularity(item, now=now)
        corroboration = CROSS_SOURCE_BONUS * max(0, item.cross_source_hits - 1)
        item.final_score = item.relevance_score + item.popularity_score + corroboration
        return item

    @staticmethod
    def _compute_popularity(item: NewsItem, now: datetime | None = None) -> float:
        """Normalize popularity + recency into a 0..10 scale across source types."""
        ref_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        score = 0.0

        if item.source_type == "hn":
            pts_part = min(7.0, math.log1p(max(0, item.raw_score)) * 1.1)
            comments_part = min(2.0, math.log1p(max(0, item.secondary_metric)) * 0.4)
            score = pts_part + comments_part
        elif item.source_type == "github":
            today_part = min(7.5, math.log1p(max(0, item.secondary_metric)) * 1.25)
            stars_part = min(2.5, math.log1p(max(0, item.raw_score)) * 0.25)
            score = today_part + stars_part
        else:
            score = 2.5 if item.source_name.startswith("ArXiv") else 4.0

        if item.published_at is not None:
            pub = item.published_at
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            age_hours = max(0.0, (ref_now - pub.astimezone(timezone.utc)).total_seconds() / 3600.0)
            if age_hours <= 6:
                score += 2.0
            elif age_hours <= 16:
                score += 1.2
            elif age_hours <= 24:
                score += 0.6

        return min(10.0, score)

    def _select_source_pinned(
        self,
        items: list[NewsItem],
        cat: CategoryConfig,
        per_cat_limit: int,
        remaining_total: int,
        used_ids: set[str],
        now: datetime | None = None,
    ) -> list[NewsItem]:
        """Select items directly by source_types/source_names and cat.sort_by."""
        allowed_types = set(cat.source_types)
        allowed_names = set(cat.source_names)

        candidates: list[tuple[tuple[float, ...], NewsItem]] = []
        for item in items:
            if item.id in used_ids:
                continue
            if allowed_types and item.source_type not in allowed_types:
                continue
            if allowed_names and item.source_name not in allowed_names:
                continue

            kw_score, matches = self._match_category_keywords(item, cat)
            rel_score = min(MAX_RELEVANCE_SCORE, kw_score) if kw_score > 0 else 0.5
            pop_score = self._compute_popularity(item, now=now)

            if cat.sort_by == "points":
                sort_key: tuple[float, ...] = (
                    float(item.raw_score),
                    float(item.secondary_metric),
                    pop_score,
                )
            elif cat.sort_by == "recent":
                pub_ts = (
                    item.published_at.timestamp()
                    if item.published_at is not None
                    else 0.0
                )
                sort_key = (pub_ts, rel_score, pop_score)
            else:
                sort_key = (rel_score + pop_score, float(item.raw_score))

            item.relevance_score = rel_score
            item.popularity_score = pop_score
            item.final_score = rel_score + pop_score
            if matches:
                item.matched_keywords = matches
            candidates.append((sort_key, item))

        candidates.sort(key=lambda pair: pair[0], reverse=True)

        # Cap at 2 per source when aggregating across multiple blogs (e.g., ds_edu),
        # but allow all items from a single pinned source (e.g., Top 10 HN or Top 3 TechCrunch).
        per_source_cap = 2 if len(cat.source_names) > 2 else per_cat_limit
        chosen: list[NewsItem] = []
        source_counts: dict[str, int] = defaultdict(int)

        for _, cand in candidates:
            if len(chosen) >= per_cat_limit or len(chosen) >= remaining_total:
                break
            if source_counts[cand.source_name] >= per_source_cap:
                continue
            cand.assigned_category = cat.key
            chosen.append(cand)
            source_counts[cand.source_name] += 1
            used_ids.add(cand.id)

        return chosen

    def classify_and_select(
        self,
        items: list[NewsItem],
        config: AppConfig,
        now: datetime | None = None,
        active_keys: set[str] | None = None,
    ) -> dict[str, list[NewsItem]]:
        """Score all items, group by category, enforce diversity, and trim to digest limits."""
        scored = [self.score_item(item, now=now) for item in items]

        grouped: dict[str, list[NewsItem]] = {cat.key: [] for cat in config.categories}
        for item in scored:
            if item.assigned_category in grouped:
                grouped[item.assigned_category].append(item)

        selected_by_cat: dict[str, list[NewsItem]] = {}
        max_per_source = config.digest.max_per_source_in_category
        total_selected = 0
        used_ids: set[str] = set()

        for cat in config.categories:
            if active_keys is not None and cat.key not in active_keys:
                continue

            per_cat_limit = cat.max_items if cat.max_items is not None else config.digest.items_per_category
            remaining_total = config.digest.max_total_items - total_selected
            if remaining_total <= 0:
                selected_by_cat[cat.key] = []
                continue

            if cat.source_types or cat.source_names:
                chosen = self._select_source_pinned(
                    items=items,
                    cat=cat,
                    per_cat_limit=per_cat_limit,
                    remaining_total=remaining_total,
                    used_ids=used_ids,
                    now=now,
                )
                selected_by_cat[cat.key] = chosen
                total_selected += len(chosen)
                continue

            candidates = sorted(
                grouped[cat.key],
                key=lambda x: (x.final_score, x.raw_score),
                reverse=True,
            )
            chosen = []
            source_counts: dict[str, int] = defaultdict(int)
            arxiv_count = 0

            for cand in candidates:
                if len(chosen) >= per_cat_limit:
                    break
                if total_selected >= config.digest.max_total_items:
                    break
                if cand.id in used_ids:
                    continue
                if source_counts[cand.source_name] >= max_per_source:
                    continue
                is_arxiv = cand.source_name.startswith("ArXiv")
                if is_arxiv and arxiv_count >= MAX_ARXIV_PER_CATEGORY:
                    continue
                chosen.append(cand)
                source_counts[cand.source_name] += 1
                used_ids.add(cand.id)
                if is_arxiv:
                    arxiv_count += 1
                total_selected += 1

            selected_by_cat[cat.key] = chosen

        return selected_by_cat
