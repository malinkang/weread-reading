"""Pure data helpers shared by the Notion sync and the static-site builder."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

try:
    from pypinyin import lazy_pinyin
except ImportError:  # pragma: no cover - optional at import time
    lazy_pinyin = None


SHANGHAI = timezone(timedelta(hours=8))
STATUS_LABELS = {"want": "想读", "reading": "在读", "finished": "读完"}
STATUS_ALIASES = {
    "想读": "want",
    "待读": "want",
    "在读": "reading",
    "阅读中": "reading",
    "读完": "finished",
    "已读": "finished",
    "完成": "finished",
}


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI)


def iso_now() -> str:
    return now_shanghai().isoformat(timespec="seconds")


def parse_date(value: Any) -> date | None:
    """Parse a Notion date, ISO date, datetime, or unix timestamp."""
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        value = value.get("start") or value.get("end")
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        if 1000 <= value <= 2100 and float(value).is_integer():
            return date(int(value), 1, 1)
        return datetime.fromtimestamp(value, SHANGHAI).date()
    if isinstance(value, datetime):
        return value.astimezone(SHANGHAI).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{10,13}", text):
        number = int(text)
        if len(text) == 13:
            number //= 1000
        return datetime.fromtimestamp(number, SHANGHAI).date()
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日", "%Y-%m", "%Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.date()
        except ValueError:
            continue
    match = re.search(r"(20\d{2}|19\d{2})", text)
    if match:
        return date(int(match.group(1)), 1, 1)
    return None


def date_text(value: Any) -> str | None:
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else None


def year_of(value: Any) -> int | None:
    parsed = parse_date(value)
    return parsed.year if parsed else None


def truncate(text: Any, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if limit > 0 and len(value) > limit:
        return value[: max(0, limit - 1)].rstrip() + "…"
    return value


def _ascii_slug(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower().replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    return normalized.strip("-")


def slugify(text: str, fallback: str = "book") -> str:
    raw = str(text or "").strip()
    slug = ""
    if lazy_pinyin is not None and raw:
        try:
            slug = "-".join(lazy_pinyin(raw, errors="ignore"))
        except Exception:
            slug = ""
    if not slug:
        slug = _ascii_slug(raw)
    slug = _ascii_slug(slug)
    if len(slug) > 72:
        slug = slug[:72].rsplit("-", 1)[0]
    return slug or fallback


def stable_slug(title: str, source_id: str, taken: set[str] | None = None) -> str:
    base = slugify(title, fallback="book")
    suffix = hashlib.sha1(str(source_id).encode("utf-8")).hexdigest()[:6]
    candidate = f"{base}-{suffix}"
    if taken is None or candidate not in taken:
        return candidate
    index = 2
    while f"{candidate}-{index}" in taken:
        index += 1
    return f"{candidate}-{index}"


def person_slug(name: str) -> str:
    return slugify(name, fallback="author")


def category_slug(name: str) -> str:
    return slugify(name, fallback="topic")


def _rich_text(content: Any) -> str:
    if not isinstance(content, list):
        return str(content or "")
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        parts.append(str(item.get("plain_text") or ""))
    return "".join(parts)


def property_value(prop: Any) -> Any:
    """Extract a useful Python value from a Notion property object."""
    if not isinstance(prop, dict):
        return prop
    prop_type = prop.get("type")
    if not prop_type:
        return None
    content = prop.get(prop_type)
    if prop_type in ("title", "rich_text"):
        return _rich_text(content)
    if prop_type in ("select", "status"):
        return (content or {}).get("name") if isinstance(content, dict) else None
    if prop_type == "multi_select":
        return [
            item.get("name")
            for item in (content or [])
            if isinstance(item, dict) and item.get("name")
        ]
    if prop_type == "number":
        return content
    if prop_type == "checkbox":
        return bool(content)
    if prop_type == "url":
        return content or None
    if prop_type == "email":
        return content or None
    if prop_type == "phone_number":
        return content or None
    if prop_type == "relation":
        return [
            item.get("id")
            for item in (content or [])
            if isinstance(item, dict) and item.get("id")
        ]
    if prop_type == "date":
        if not isinstance(content, dict):
            return None
        return {"start": content.get("start"), "end": content.get("end")}
    if prop_type == "files":
        files = []
        for item in content or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "external":
                url = (item.get("external") or {}).get("url")
            else:
                url = (item.get("file") or {}).get("url")
            if url:
                files.append(url)
        return files
    if prop_type == "formula":
        if not isinstance(content, dict):
            return None
        formula_type = content.get("type")
        return content.get(formula_type) if formula_type else None
    if prop_type == "rollup":
        if not isinstance(content, dict):
            return None
        rollup_type = content.get("type")
        return content.get(rollup_type) if rollup_type else None
    return content


def prop_from(
    props: dict[str, Any],
    names: Iterable[str],
    *,
    title_key: str | None = None,
    default: Any = None,
) -> Any:
    for name in names:
        if name and name in props:
            value = property_value(props.get(name))
            if value not in (None, "", [], {}):
                return value
    if title_key and title_key in props:
        value = property_value(props.get(title_key))
        if value not in (None, "", [], {}):
            return value
    return default


def as_text(value: Any, limit: int = 0) -> str:
    if isinstance(value, list):
        value = " / ".join(str(item) for item in value if item)
    return truncate(value, limit) if limit else str(value or "").strip()


def as_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def normalize_status(value: Any, progress: float = 0, reading_seconds: int = 0) -> str:
    text = as_text(value)
    if text in STATUS_ALIASES:
        return STATUS_ALIASES[text]
    lowered = text.lower()
    if lowered in ("finished", "done", "read"):
        return "finished"
    if lowered in ("reading", "in_progress"):
        return "reading"
    if lowered in ("want", "to_read", "planned"):
        return "want"
    if progress >= 0.999:
        return "finished"
    if progress > 0 or reading_seconds >= 60:
        return "reading"
    return "want"


def normalize_progress(value: Any) -> float:
    number = as_number(value)
    if number is None:
        return 0.0
    if number > 1:
        number /= 100.0
    return round(max(0.0, min(1.0, number)), 4)


def normalize_my_rating(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = int(round(float(value)))
        return number if 1 <= number <= 5 else None
    text = as_text(value)
    stars = text.count("⭐") + text.count("★")
    if stars:
        return min(stars, 5)
    number = as_number(text)
    if number is None:
        return None
    number = int(round(number))
    return number if 1 <= number <= 5 else None


def normalize_public_rating(value: Any) -> float | None:
    number = as_number(value)
    if number is None or number <= 0:
        return None
    if number > 10:
        number /= 10.0
    return round(min(number, 10.0), 2)


def reading_time_label(seconds: Any) -> str:
    total = int(as_number(seconds) or 0)
    if total <= 0:
        return "未记录"
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours} 小时 {minutes} 分钟"
    if hours:
        return f"{hours} 小时"
    return f"{max(1, minutes)} 分钟"


def reading_time_compact(seconds: Any) -> str:
    total = int(as_number(seconds) or 0)
    if total <= 0:
        return "未记录"
    hours = total / 3600
    if hours >= 10:
        return f"{hours:.0f} 小时"
    if hours >= 1:
        return f"{hours:.1f} 小时"
    return f"{max(1, total // 60)} 分钟"


def compact_number(value: Any) -> str:
    number = int(as_number(value) or 0)
    if number >= 10000:
        return f"{number / 10000:.1f} 万"
    if number >= 1000:
        return f"{number / 1000:.1f}k"
    return str(number)


def percentile_level(value: float, maximum: float) -> int:
    if value <= 0 or maximum <= 0:
        return 0
    ratio = value / maximum
    if ratio >= 0.75:
        return 4
    if ratio >= 0.5:
        return 3
    if ratio >= 0.25:
        return 2
    return 1


def compute_heatmap(daily: list[dict[str, Any]], days: int = 371) -> list[dict[str, Any]]:
    by_date = {
        str(item.get("date")): int(item.get("seconds") or 0)
        for item in daily
        if item.get("date")
    }
    today = now_shanghai().date()
    # End on the Saturday of the current week, then go back a whole number
    # of weeks so the grid always contains exactly ``days`` cells.
    end = today + timedelta(days=(5 - today.weekday()) % 7)
    start = end - timedelta(days=days - 1)
    maximum = max(by_date.values(), default=0)
    cells: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        seconds = by_date.get(cursor.isoformat(), 0)
        cells.append(
            {
                "date": cursor.isoformat(),
                "seconds": seconds,
                "level": percentile_level(seconds, maximum),
                "future": cursor > today,
            }
        )
        cursor += timedelta(days=1)
    return cells


def compute_stats(books: list[dict[str, Any]], daily: list[dict[str, Any]]) -> dict[str, Any]:
    finished = [book for book in books if book.get("status") == "finished"]
    reading = [book for book in books if book.get("status") == "reading"]
    want = [book for book in books if book.get("status") == "want"]
    total_seconds = sum(int(book.get("reading_seconds") or 0) for book in books)
    total_highlights = sum(int(book.get("highlight_count") or 0) for book in books)
    total_notes = sum(int(book.get("note_count") or 0) for book in books)
    total_words = sum(int(book.get("words") or 0) for book in books)

    daily_by_date: dict[str, int] = {}
    for item in daily:
        date_key = str(item.get("date") or "")
        if date_key:
            daily_by_date[date_key] = daily_by_date.get(date_key, 0) + int(
                item.get("seconds") or 0
            )

    by_year: dict[int, dict[str, Any]] = {}
    daily_years = {
        parsed.year
        for date_key in daily_by_date
        if (parsed := parse_date(date_key)) is not None
    }
    for book in books:
        finished_year = year_of(book.get("finished_at"))
        if finished_year:
            entry = by_year.setdefault(
                finished_year, {"year": finished_year, "seconds": 0, "finished": 0}
            )
            entry["finished"] += 1
            if finished_year not in daily_years:
                entry["seconds"] += int(book.get("reading_seconds") or 0)
    for date_key, seconds in daily_by_date.items():
        parsed = parse_date(date_key)
        if not parsed:
            continue
        entry = by_year.setdefault(
            parsed.year, {"year": parsed.year, "seconds": 0, "finished": 0}
        )
        entry["seconds"] += seconds

    by_category: dict[str, dict[str, Any]] = {}
    for book in books:
        categories = book.get("categories") or ["未分类"]
        for name in categories:
            entry = by_category.setdefault(
                name,
                {
                    "name": name,
                    "slug": category_slug(name),
                    "books": 0,
                    "seconds": 0,
                },
            )
            entry["books"] += 1
            entry["seconds"] += int(book.get("reading_seconds") or 0)

    top_books = sorted(
        books,
        key=lambda item: (
            int(item.get("reading_seconds") or 0),
            int(item.get("highlight_count") or 0),
        ),
        reverse=True,
    )[:8]
    top_categories = sorted(
        by_category.values(), key=lambda item: (item["seconds"], item["books"]), reverse=True
    )
    years = sorted(by_year.values(), key=lambda item: item["year"])

    return {
        "books": len(books),
        "finished": len(finished),
        "reading": len(reading),
        "want": len(want),
        "reading_seconds": total_seconds,
        "reading_time_label": reading_time_label(total_seconds),
        "unique_reading_days": len(daily_by_date),
        "highlights": total_highlights,
        "notes": total_notes,
        "words": total_words,
        "authors": len(
            {
                author
                for book in books
                for author in (book.get("authors") or [])
                if author
            }
        ),
        "categories": len(by_category),
        "by_year": years,
        "by_category": top_categories,
        "top_books": [
            {
                "slug": book.get("slug"),
                "title": book.get("title"),
                "authors": book.get("authors") or [],
                "reading_seconds": int(book.get("reading_seconds") or 0),
                "reading_time_label": reading_time_label(book.get("reading_seconds")),
            }
            for book in top_books
        ],
        "heatmap": compute_heatmap(daily),
    }


def aggregate_entities(books: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    entities: dict[str, dict[str, Any]] = {}
    for book in books:
        for name in book.get(field) or []:
            if not name:
                continue
            entry = entities.setdefault(
                name,
                {
                    "name": name,
                    "slug": person_slug(name) if field == "authors" else category_slug(name),
                    "books": 0,
                    "seconds": 0,
                },
            )
            entry["books"] += 1
            entry["seconds"] += int(book.get("reading_seconds") or 0)
    return sorted(
        entities.values(), key=lambda item: (item["books"], item["seconds"]), reverse=True
    )


def normalize_daily(pages: Iterable[dict[str, Any]], title_key: str | None = None) -> list[dict[str, Any]]:
    totals: dict[str, int] = {}
    for page in pages:
        props = page.get("properties") or {}
        date_value = prop_from(props, ("日期", "Date", "时间", "Title"), title_key=title_key)
        parsed = parse_date(date_value)
        if not parsed:
            continue
        seconds = as_number(
            prop_from(props, ("时长", "阅读时长", "Duration", "Seconds"), default=0)
        )
        totals[parsed.isoformat()] = totals.get(parsed.isoformat(), 0) + int(seconds or 0)
    return [
        {"date": date_key, "seconds": seconds}
        for date_key, seconds in sorted(totals.items())
        if seconds > 0
    ]


def ensure_public_url(url: str | None) -> str | None:
    if not url:
        return None
    value = str(url).strip()
    if value.startswith(("http://", "https://")):
        return value
    return None


def make_search_index(books: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "slug": book["slug"],
            "title": book.get("title") or "",
            "authors": book.get("authors") or [],
            "categories": book.get("categories") or [],
            "cover_url": book.get("cover_url") or "",
            "status": book.get("status") or "want",
            "status_label": STATUS_LABELS.get(book.get("status") or "want", "想读"),
            "year": book.get("finished_at", "")[:4] if book.get("finished_at") else "",
        }
        for book in books
    ]
