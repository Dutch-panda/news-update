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

# Tie-break priority independent of display order: specialized categories win ties over broad news
SPECIFICITY_ORDER = (
    "ai_agents",
    "llm_models",
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
        self._patterns: dict[str, list[tuple[str, re.Pattern[str]]]] = {
            cat.key: [(kw, _compile_keyword_pattern(kw)) for kw in cat.keywords]
            for cat in categories
        }
        self._valid_keys = {cat.key for cat in categories}
        self._specificity_rank = {key: idx for idx, key in enumerate(SPECIFICITY_ORDER)}

    def score_item(self, item: NewsItem, now: datetime | None = None) -> NewsItem:
        """Assign category, relevance score, popularity score, and final score to an item."""
        title = item.title or ""
        summary = item.summary or ""

        scores_by_cat: dict[str, float] = {}
        matched_by_cat: dict[str, list[str]] = {}

        for cat in self.categories:
            cat_score = 0.0
            cat_matches: list[str] = []
            for kw, pattern in self._patterns[cat.key]:
                in_title = bool(pattern.search(title))
                in_summary = bool(pattern.search(summary))
                if in_title or in_summary:
                    cat_matches.append(kw)
                    if in_title:
                        cat_score += TITLE_MATCH_WEIGHT
                    if in_summary:
                        cat_score += SUMMARY_MATCH_WEIGHT
            if cat_score > 0:
                scores_by_cat[cat.key] = cat_score
                matched_by_cat[cat.key] = cat_matches

        # Structural priors for developer tools / repos when they don't strongly match AI/LLM
        if "new_tools" in self._valid_keys:
            if item.source_type == "github":
                scores_by_cat["new_tools"] = scores_by_cat.get("new_tools", 0.0) + GITHUB_TOOLS_PRIOR
            elif title.lower().startswith("show hn:"):
                scores_by_cat["new_tools"] = scores_by_cat.get("new_tools", 0.0) + SHOW_HN_TOOLS_PRIOR

        if scores_by_cat:
            # Break ties by specificity rank rather than display order
            best_cat = max(
                self.categories,
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
                if item.default_category in self._valid_keys
                else self.categories[0].key
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
            # HN points typically range 10..1500; log1p(500) ~ 6.2
            pts_part = min(7.0, math.log1p(max(0, item.raw_score)) * 1.1)
            comments_part = min(2.0, math.log1p(max(0, item.secondary_metric)) * 0.4)
            score = pts_part + comments_part
        elif item.source_type == "github":
            # Stars today (secondary_metric) is strongest signal for daily trending
            today_part = min(6.0, math.log1p(max(0, item.secondary_metric)) * 1.0)
            stars_part = min(4.0, math.log1p(max(0, item.raw_score)) * 0.45)
            score = today_part + stars_part
        else:
            # RSS articles lack point counts; base score on freshness
            score = 4.0

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

    def classify_and_select(
        self,
        items: list[NewsItem],
        config: AppConfig,
        now: datetime | None = None,
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

        for cat in config.categories:
            per_cat_limit = cat.max_items if cat.max_items is not None else config.digest.items_per_category
            candidates = sorted(
                grouped[cat.key],
                key=lambda x: (x.final_score, x.raw_score),
                reverse=True,
            )
            chosen: list[NewsItem] = []
            source_counts: dict[str, int] = defaultdict(int)

            for cand in candidates:
                if len(chosen) >= per_cat_limit:
                    break
                if total_selected >= config.digest.max_total_items:
                    break
                if source_counts[cand.source_name] >= max_per_source:
                    continue
                chosen.append(cand)
                source_counts[cand.source_name] += 1
                total_selected += 1

            selected_by_cat[cat.key] = chosen

        return selected_by_cat
