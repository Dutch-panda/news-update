"""Deduplication utilities for URL normalization, title/summary fingerprinting, and fuzzy matching."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from src.models import NewsItem

TRACKING_PARAMS_PREFIX = ("utm_", "fbclid", "gclid", "mc_", "ref", "source")
NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]+")
WHITESPACE_RE = re.compile(r"\s+")

SUMMARY_DEDUP_MIN_CHARS = 40
SUMMARY_DEDUP_PREFIX_CHARS = 90
TITLE_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "its",
        "over",
        "new",
        "now",
        "how",
        "why",
        "what",
        "who",
        "show",
        "ask",
        "hn",
        "live",
        "update",
        "updates",
        "latest",
        "news",
        "says",
        "said",
        "after",
        "amid",
        "about",
        "will",
        "can",
    }
)


def normalize_url(url: str) -> str:
    """Normalize a URL by removing tracking query params, fragments, www., and trailing slashes."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    filtered_query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if not any(k.lower().startswith(prefix) for prefix in TRACKING_PARAMS_PREFIX)
    ]
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (
            parsed.scheme.lower() or "https",
            netloc,
            path,
            "",
            urlencode(filtered_query, doseq=True),
            "",
        )
    )


def normalize_title(title: str) -> str:
    """Normalize a title into a canonical lowercase alphanumeric string."""
    cleaned = NON_ALNUM_RE.sub(" ", (title or "").lower())
    return WHITESPACE_RE.sub(" ", cleaned).strip()


def title_fingerprint(title: str) -> str:
    """Return a stable SHA-1 fingerprint for a normalized title."""
    norm = normalize_title(title)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def url_fingerprint(url: str) -> str:
    """Return a stable SHA-1 fingerprint for a normalized URL."""
    norm = normalize_url(url)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def summary_prefix_fingerprint(summary: str) -> str:
    """Return a SHA-1 fingerprint of the normalized summary prefix for cloned/spam detection."""
    norm = normalize_title(summary)
    if len(norm) < SUMMARY_DEDUP_MIN_CHARS:
        return ""
    prefix = norm[:SUMMARY_DEDUP_PREFIX_CHARS]
    return hashlib.sha1(prefix.encode("utf-8")).hexdigest()


def significant_title_tokens(title: str) -> set[str]:
    """Extract significant content tokens (length >= 3, non-stopword) from a title."""
    tokens = normalize_title(title).split()
    return {tok for tok in tokens if len(tok) >= 3 and tok not in TITLE_STOPWORDS}


def _tokens_are_fuzzy_match(tokens_a: set[str], tokens_b: set[str], min_shared: int = 3) -> bool:
    """Return True if two token sets share substantial topical overlap."""
    if not tokens_a or not tokens_b:
        return False
    shared = len(tokens_a & tokens_b)
    if shared < min_shared:
        return False
    union = len(tokens_a | tokens_b)
    min_len = min(len(tokens_a), len(tokens_b))
    jaccard = shared / union
    overlap = shared / min_len
    return jaccard >= 0.55 or overlap >= 0.75


def _item_priority_key(item: NewsItem) -> tuple[int, int, int]:
    """Rank which duplicate item to keep when merging."""
    return (item.raw_score, item.secondary_metric, len(item.summary or ""))


class DedupStore:
    """Persistent store of previously delivered URLs, titles, and summary fingerprints."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.seen: dict[str, str] = {}
        self.load()

    def load(self) -> None:
        """Load seen keys from disk if the file exists."""
        if not self.path.exists():
            self.seen = {}
            return
        with self.path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        raw_seen = data.get("seen", {}) if isinstance(data, dict) else {}
        self.seen = {str(k): str(v) for k, v in raw_seen.items()}

    def save(self) -> None:
        """Persist seen keys to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump({"seen": self.seen}, fh, indent=2, sort_keys=True)
            fh.write("\n")

    def is_seen(self, item: NewsItem) -> bool:
        """Return True if an item's URL, title, or summary prefix was already seen."""
        if item.url and f"url:{url_fingerprint(item.url)}" in self.seen:
            return True
        if item.title and f"title:{title_fingerprint(item.title)}" in self.seen:
            return True
        s_fp = summary_prefix_fingerprint(item.summary)
        if s_fp and f"sum:{s_fp}" in self.seen:
            return True
        return False

    def filter_unseen(self, items: list[NewsItem], ignore_history: bool = False) -> list[NewsItem]:
        """Filter out seen items, cloned summaries, and fuzzy title/summary duplicates within the batch."""
        unique: list[NewsItem] = []
        unique_title_tokens: list[set[str]] = []
        unique_summary_tokens: list[set[str]] = []
        batch_keys: set[str] = set()
        history = {} if ignore_history else self.seen

        # Process higher-priority items first so the strongest representative survives fuzzy merging
        ordered = sorted(items, key=_item_priority_key, reverse=True)

        for item in ordered:
            u_key = f"url:{url_fingerprint(item.url)}" if item.url else ""
            t_key = f"title:{title_fingerprint(item.title)}" if item.title else ""
            s_fp = summary_prefix_fingerprint(item.summary)
            s_key = f"sum:{s_fp}" if s_fp else ""

            if any(k and (k in history or k in batch_keys) for k in (u_key, t_key, s_key)):
                continue

            title_tokens = significant_title_tokens(item.title)
            summary_tokens = significant_title_tokens(item.summary)
            matched_idx: int | None = None
            for idx, (existing_t, existing_s) in enumerate(zip(unique_title_tokens, unique_summary_tokens)):
                if _tokens_are_fuzzy_match(title_tokens, existing_t, min_shared=3):
                    matched_idx = idx
                    break
                if len(summary_tokens) >= 6 and len(existing_s) >= 6 and _tokens_are_fuzzy_match(
                    summary_tokens, existing_s, min_shared=5
                ):
                    matched_idx = idx
                    break

            if matched_idx is not None:
                # Corroborate surviving item if reported by a different source
                survivor = unique[matched_idx]
                if survivor.source_name != item.source_name:
                    survivor.cross_source_hits += 1
                if len(item.summary or "") > len(survivor.summary or ""):
                    survivor.summary = item.summary
                continue

            for k in (u_key, t_key, s_key):
                if k:
                    batch_keys.add(k)
            unique.append(item)
            unique_title_tokens.append(title_tokens)
            unique_summary_tokens.append(summary_tokens)

        return unique

    def mark_seen(self, items: list[NewsItem], now: datetime | None = None) -> None:
        """Record selected items as seen with an ISO-8601 UTC timestamp."""
        ts = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        for item in items:
            if item.url:
                self.seen[f"url:{url_fingerprint(item.url)}"] = ts
            if item.title:
                self.seen[f"title:{title_fingerprint(item.title)}"] = ts
            s_fp = summary_prefix_fingerprint(item.summary)
            if s_fp:
                self.seen[f"sum:{s_fp}"] = ts

    def prune(self, retention_days: int, now: datetime | None = None) -> int:
        """Remove entries older than retention_days. Returns number of removed keys."""
        if retention_days <= 0:
            return 0
        cutoff = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - timedelta(
            days=retention_days
        )
        kept: dict[str, str] = {}
        removed = 0
        for key, iso_ts in self.seen.items():
            try:
                parsed = datetime.fromisoformat(iso_ts)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                if parsed >= cutoff:
                    kept[key] = iso_ts
                else:
                    removed += 1
            except ValueError:
                removed += 1
        self.seen = kept
        return removed
