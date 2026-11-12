"""Pull WeRead's Notion databases and produce a sanitized public snapshot."""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .model import (
    aggregate_entities,
    as_number,
    as_text,
    category_slug,
    compute_stats,
    date_text,
    ensure_public_url,
    iso_now,
    normalize_daily,
    normalize_my_rating,
    normalize_progress,
    normalize_public_rating,
    normalize_status,
    parse_date,
    prop_from,
    reading_time_label,
    stable_slug,
    STATUS_LABELS,
)
from .notion_api import NotionAPI, NotionAPIError, find_title_key, page_icon_url


LOG = logging.getLogger("weread-reading.sync")
READING_LOG_RELATION_NAMES = (
    "阅读记录",
    "Reading Log",
    "ReadingLog",
    "Reading log",
)


@dataclass
class SyncConfig:
    token: str
    books_data_source_id: str
    authors_data_source_id: str | None = None
    categories_data_source_id: str | None = None
    bookmarks_data_source_id: str | None = None
    reviews_data_source_id: str | None = None
    chapters_data_source_id: str | None = None
    daily_data_source_id: str | None = None
    reading_log_data_source_id: str | None = None
    include_highlights: bool = True
    include_reviews: bool = False
    max_highlights_per_book: int = 40
    max_highlight_chars: int = 500
    site_title: str = "读书记"
    site_url: str = ""
    site_description: str = "我的微信读书书架，按阅读时间、评分和划线整理。"
    notion_version: str = "2025-09-03"
    request_interval: float = 0.34


def _env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value and value.strip() not in {"undefined", "null", "[undefined]", "[null]"}:
            return value.strip()
    return None


def _bool_env(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    value = _env(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise SystemExit(f"{name} 必须是整数，当前值为 {value!r}") from error
    return max(minimum, parsed)


def config_from_env() -> SyncConfig:
    books = _env(
        "WEREAD_BOOKS_DATA_SOURCE_ID",
        "WEREAD_BOOKS_DATABASE_ID",
        "BOOKSHELF_DATA_SOURCE_ID",
        "BOOKSHELF_DATABASE_ID",
        "NOTION_BOOKS_DATA_SOURCE_ID",
        "NOTION_DATABASE_ID",
    )
    if not books:
        raise SystemExit(
            "缺少 WEREAD_BOOKS_DATA_SOURCE_ID 或 WEREAD_BOOKS_DATABASE_ID"
        )
    return SyncConfig(
        token=_env("NOTION_TOKEN") or "",
        books_data_source_id=books,
        authors_data_source_id=_env(
            "WEREAD_AUTHORS_DATA_SOURCE_ID",
            "WEREAD_AUTHORS_DATABASE_ID",
            "AUTHOR_DATA_SOURCE_ID",
            "AUTHOR_DATABASE_ID",
        ),
        categories_data_source_id=_env(
            "WEREAD_CATEGORIES_DATA_SOURCE_ID",
            "WEREAD_CATEGORIES_DATABASE_ID",
            "CATEGORY_DATA_SOURCE_ID",
            "CATEGORY_DATABASE_ID",
        ),
        bookmarks_data_source_id=_env(
            "WEREAD_BOOKMARKS_DATA_SOURCE_ID",
            "WEREAD_BOOKMARKS_DATABASE_ID",
            "BOOKMARK_DATA_SOURCE_ID",
            "BOOKMARK_DATABASE_ID",
        ),
        reviews_data_source_id=_env(
            "WEREAD_REVIEWS_DATA_SOURCE_ID",
            "WEREAD_REVIEWS_DATABASE_ID",
            "REVIEW_DATA_SOURCE_ID",
            "REVIEW_DATABASE_ID",
        ),
        chapters_data_source_id=_env(
            "WEREAD_CHAPTERS_DATA_SOURCE_ID",
            "WEREAD_CHAPTERS_DATABASE_ID",
            "CHAPTER_DATA_SOURCE_ID",
            "CHAPTER_DATABASE_ID",
        ),
        daily_data_source_id=_env(
            "WEREAD_DAILY_DATA_SOURCE_ID",
            "WEREAD_DAILY_DATABASE_ID",
            "DAY_DATA_SOURCE_ID",
            "DAY_DATABASE_ID",
        ),
        reading_log_data_source_id=_env(
            "WEREAD_READING_LOG_DATA_SOURCE_ID",
            "WEREAD_READING_LOG_DATABASE_ID",
            "READING_LOG_DATA_SOURCE_ID",
            "READING_LOG_DATABASE_ID",
        ),
        include_highlights=_bool_env(os.getenv("PUBLIC_INCLUDE_HIGHLIGHTS"), True),
        include_reviews=_bool_env(os.getenv("PUBLIC_INCLUDE_REVIEWS"), False),
        max_highlights_per_book=_int_env(
            "PUBLIC_MAX_HIGHLIGHTS_PER_BOOK", 40, minimum=0
        ),
        max_highlight_chars=_int_env("PUBLIC_MAX_HIGHLIGHT_CHARS", 500, minimum=1),
        site_title=_env("PUBLIC_SITE_TITLE") or "读书记",
        site_url=(_env("PUBLIC_SITE_URL") or "").rstrip("/"),
        site_description=(
            _env("PUBLIC_SITE_DESCRIPTION")
            or "我的微信读书书架，按阅读时间、评分和划线整理。"
        ),
        notion_version=_env("NOTION_VERSION") or "2025-09-03",
        request_interval=float(_env("NOTION_REQUEST_INTERVAL") or "0.34"),
    )


def find_relation_data_source(
    data_source: dict[str, Any], names: Iterable[str]
) -> str | None:
    properties = data_source.get("properties") or {}
    for name in names:
        prop = properties.get(name)
        if not isinstance(prop, dict) or prop.get("type") != "relation":
            continue
        relation = prop.get("relation") or {}
        target = relation.get("data_source_id") or relation.get("database_id")
        if target:
            return str(target)
    return None


def _select_reading_log_data_source(
    books_data_source: dict[str, Any],
    configured_data_source_id: str | None,
) -> tuple[str | None, bool]:
    discovered = find_relation_data_source(
        books_data_source,
        READING_LOG_RELATION_NAMES,
    )
    if configured_data_source_id:
        return configured_data_source_id, False
    return discovered, bool(discovered)


def _optional_pages(
    api: NotionAPI,
    data_source_id: str | None,
    label: str,
    *,
    data_source: dict[str, Any] | None = None,
    strict: bool = True,
) -> tuple[list[dict[str, Any]], str | None]:
    if not data_source_id:
        LOG.info("skip %s: data source not configured", label)
        return [], None
    LOG.info("fetch %s (%s)", label, data_source_id)
    try:
        if data_source is None:
            resolved_id = api.resolve_data_source_id(data_source_id)
            data_source = api.retrieve_data_source(resolved_id)
        title_key = find_title_key(data_source)
        pages = api.query_all(data_source_id)
    except NotionAPIError as error:
        if strict or error.status_code not in {400, 403, 404}:
            raise
        LOG.warning("skip %s: %s", label, error)
        return [], None
    return pages, title_key


def _page_title(props: dict[str, Any], title_key: str | None) -> str:
    value = prop_from(
        props,
        ("书名", "标题", "名称", "Name", "Title", "content", "markText"),
        title_key=title_key,
    )
    return as_text(value)


def _relation_ids(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _split_people(value: Any) -> list[str]:
    if isinstance(value, list):
        return [as_text(item) for item in value if as_text(item)]
    text = as_text(value)
    if not text:
        return []
    return [part.strip() for part in re_split_people(text) if part.strip()]


def re_split_people(text: str) -> list[str]:
    for separator in (" / ", "、", "；", ";", "，", ","):
        if separator in text:
            return text.split(separator)
    return [text]


def _entity_map(
    pages: Iterable[dict[str, Any]], title_key: str | None
) -> dict[str, str]:
    result: dict[str, str] = {}
    for page in pages:
        title = _page_title(page.get("properties") or {}, title_key)
        if title:
            result[str(page.get("id"))] = title
    return result


def _entity_names(
    props: dict[str, Any],
    relation_names: Iterable[str],
    text_names: Iterable[str],
    lookup: dict[str, str],
) -> list[str]:
    relation_ids = _relation_ids(prop_from(props, relation_names))
    names = [lookup.get(page_id) for page_id in relation_ids]
    names = [name for name in names if name]
    if names:
        return list(dict.fromkeys(names))
    raw = prop_from(props, text_names)
    return list(dict.fromkeys(_split_people(raw)))


def _extract_book(
    page: dict[str, Any],
    *,
    title_key: str | None,
    author_lookup: dict[str, str],
    category_lookup: dict[str, str],
    taken_slugs: set[str],
) -> dict[str, Any] | None:
    props = page.get("properties") or {}
    title = _page_title(props, title_key)
    if not title:
        return None
    kind = as_text(prop_from(props, ("类型", "Type", "Kind")))
    if kind and kind.lower() not in {"书籍", "book", "books"}:
        return None
    source_id = as_text(
        prop_from(props, ("BookId", "bookId", "书籍ID", "ID"), default=page.get("id"))
    )
    authors = _entity_names(
        props,
        ("作者", "Authors", "Author"),
        ("作者", "作者名", "作者文本", "author"),
        author_lookup,
    )
    categories = _entity_names(
        props,
        ("分类", "Categories", "Category", "标签"),
        ("分类", "标签", "分类名", "分类文本", "category"),
        category_lookup,
    )
    status_raw = prop_from(props, ("阅读状态", "状态", "Status"))
    progress = normalize_progress(
        prop_from(props, ("阅读进度", "进度", "Progress"), default=0)
    )
    reading_seconds = int(
        as_number(prop_from(props, ("阅读时长", "时长", "ReadingTime"), default=0)) or 0
    )
    status = normalize_status(status_raw, progress, reading_seconds)
    cover_urls = prop_from(props, ("封面", "Cover", "封面图"), default=[])
    cover = cover_urls[0] if isinstance(cover_urls, list) and cover_urls else None
    cover = ensure_public_url(cover) or ensure_public_url(page_icon_url(page))
    published_value = prop_from(
        props, ("出版日期", "出版年份", "Published", "Year")
    )
    if isinstance(published_value, (int, float)) and 1000 <= published_value <= 2100:
        published = str(int(published_value))
    elif re.fullmatch(r"(19|20)\d{2}", as_text(published_value)):
        published = as_text(published_value)
    else:
        published = date_text(published_value) or as_text(published_value)
    if published and len(published) > 10:
        published = published[:10]
    intro = as_text(
        prop_from(props, ("简介", "内容简介", "Description", "Summary")), limit=1600
    )
    started_at = date_text(
        prop_from(props, ("开始阅读时间", "StartedAt", "开始时间"))
    )
    last_read_at = date_text(
        prop_from(props, ("最后阅读时间", "LastReadAt", "最近阅读时间"))
    )
    finished_raw = prop_from(
        props, ("完成时间", "读完时间", "FinishedAt", "时间", "Date")
    )
    finished_at = date_text(finished_raw) if status == "finished" else None
    if status == "finished" and not finished_at:
        finished_at = last_read_at
    my_rating = normalize_my_rating(
        prop_from(props, ("我的评分", "MyRating", "个人评分"))
    )
    rating = normalize_public_rating(
        prop_from(props, ("评分", "推荐值", "Rating", "Score"))
    )
    slug = stable_slug(title, source_id, taken_slugs)
    taken_slugs.add(slug)
    return {
        "source_id": source_id,
        "page_id": str(page.get("id")),
        "slug": slug,
        "title": title,
        "authors": authors or ["未知作者"],
        "categories": categories,
        "cover": cover,
        "isbn": as_text(prop_from(props, ("ISBN", "isbn"))),
        "publisher": as_text(prop_from(props, ("出版社", "Publisher"))),
        "published": published,
        "rating": rating,
        "my_rating": my_rating,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "progress": 1.0 if status == "finished" else progress,
        "reading_seconds": reading_seconds,
        "reading_time_label": reading_time_label(reading_seconds),
        "reading_days": int(
            as_number(prop_from(props, ("阅读天数", "ReadingDays"), default=0)) or 0
        ),
        "words": int(as_number(prop_from(props, ("字数", "Words"), default=0)) or 0),
        "price": as_number(prop_from(props, ("价格", "Price"))),
        "intro": intro,
        "link": ensure_public_url(prop_from(props, ("链接", "Link", "微信读书链接"))),
        "started_at": started_at,
        "finished_at": finished_at,
        "last_read_at": last_read_at,
        "highlight_count": 0,
        "note_count": 0,
        "chapter_count": 0,
        "highlights": [],
    }


def _chapter_lookup(
    pages: Iterable[dict[str, Any]], title_key: str | None
) -> dict[tuple[str, str], str]:
    lookup: dict[tuple[str, str], str] = {}
    for page in pages:
        props = page.get("properties") or {}
        book_ids = _relation_ids(prop_from(props, ("书籍", "Book", "Books")))
        chapter_uid = _chapter_uid(prop_from(props, ("chapterUid", "章节ID")))
        name = _page_title(props, title_key) or as_text(
            prop_from(props, ("章节名", "Chapter", "Name"))
        )
        if not book_ids or not chapter_uid or not name:
            continue
        for book_id in book_ids:
            lookup[(book_id, chapter_uid)] = name
    return lookup


def _chapter_uid(value: Any) -> str:
    number = as_number(value)
    if number is not None and float(number).is_integer():
        return str(int(number))
    return as_text(value)


def _attach_annotations(
    books: list[dict[str, Any]],
    page_to_book: dict[str, dict[str, Any]],
    bookmark_pages: Iterable[dict[str, Any]],
    review_pages: Iterable[dict[str, Any]],
    chapters: dict[tuple[str, str], str],
    config: SyncConfig,
    bookmark_title_key: str | None = None,
    review_title_key: str | None = None,
) -> None:
    for book in books:
        book["_chapter_names"] = set()

    def add(page: dict[str, Any], kind: str, title_key: str | None = None) -> None:
        props = page.get("properties") or {}
        book_page_ids = _relation_ids(prop_from(props, ("书籍", "Book", "Books")))
        text = _page_title(props, title_key)
        if not text:
            text = as_text(prop_from(props, ("内容", "划线内容", "正文", "Text")))
        if not text:
            return
        text = as_text(text, limit=config.max_highlight_chars)
        chapter_uid = _chapter_uid(prop_from(props, ("chapterUid", "章节ID")))
        created = date_text(prop_from(props, ("Date", "日期", "创建时间")))
        note = as_text(prop_from(props, ("abstract", "想法", "笔记", "Note")), limit=800)
        for book_page_id in book_page_ids:
            book = page_to_book.get(book_page_id)
            if not book:
                continue
            chapter = chapters.get((book_page_id, chapter_uid)) or "未分章"
            book["_chapter_names"].add(chapter)
            if kind == "highlight":
                book["highlight_count"] += 1
            else:
                book["note_count"] += 1
            if len(book["highlights"]) >= config.max_highlights_per_book:
                continue
            book["highlights"].append(
                {
                    "kind": kind,
                    "text": text,
                    "note": note,
                    "chapter": chapter,
                    "created_at": created,
                }
            )

    if config.include_highlights:
        for page in bookmark_pages:
            add(page, "highlight", bookmark_title_key)
    if config.include_reviews:
        for page in review_pages:
            add(page, "note", review_title_key)
    for book in books:
        book["chapter_count"] = len(book.pop("_chapter_names", set()))
        book["highlights"].sort(
            key=lambda item: (
                item.get("chapter") or "",
                item.get("created_at") or "",
                item.get("text") or "",
            )
        )


def _attach_book_daily(
    books: list[dict[str, Any]],
    book_lookup: dict[str, dict[str, Any]],
    pages: Iterable[dict[str, Any]],
    title_key: str | None = None,
) -> int:
    totals: dict[str, dict[str, int]] = {}
    for page in pages:
        props = page.get("properties") or {}
        book_keys = _relation_ids(
            prop_from(props, ("书架", "书籍", "Book", "Books"))
        )
        if not book_keys:
            book_keys = _relation_ids(
                prop_from(props, ("BookId", "bookId", "书籍ID"))
            )
        if not book_keys:
            continue
        parsed = parse_date(
            prop_from(
                props,
                ("日期", "Date", "时间", "Title"),
                title_key=title_key,
            )
        )
        if not parsed:
            continue
        seconds = int(
            as_number(
                prop_from(
                    props,
                    ("时长", "阅读时长", "Duration", "Seconds"),
                    default=0,
                )
            )
            or 0
        )
        if seconds <= 0:
            continue
        book = next(
            (book_lookup[key] for key in book_keys if key in book_lookup),
            None,
        )
        if not book:
            continue
        day_totals = totals.setdefault(book["page_id"], {})
        day_key = parsed.isoformat()
        day_totals[day_key] = day_totals.get(day_key, 0) + seconds

    record_count = 0
    for book in books:
        day_totals = totals.get(book["page_id"])
        if not day_totals:
            continue
        reading_daily = [
            {"date": day, "seconds": seconds}
            for day, seconds in sorted(day_totals.items())
        ]
        book["reading_daily"] = reading_daily
        if not int(book.get("reading_days") or 0):
            book["reading_days"] = len(reading_daily)
        record_count += len(reading_daily)
    return record_count


def build_snapshot(config: SyncConfig, api: NotionAPI | None = None) -> dict[str, Any]:
    api = api or NotionAPI(
        config.token,
        version=config.notion_version,
        request_interval=config.request_interval,
    )
    books_data_source = api.retrieve_data_source(
        api.resolve_data_source_id(config.books_data_source_id)
    )
    books_pages, books_title_key = _optional_pages(
        api,
        config.books_data_source_id,
        "books",
        data_source=books_data_source,
    )
    authors_data_source_id = config.authors_data_source_id or find_relation_data_source(
        books_data_source, ("作者", "Authors", "Author")
    )
    categories_data_source_id = (
        config.categories_data_source_id
        or find_relation_data_source(
            books_data_source, ("分类", "Categories", "Category", "标签")
        )
    )
    if authors_data_source_id and not config.authors_data_source_id:
        LOG.info("authors data source auto-discovered from books relation")
    if categories_data_source_id and not config.categories_data_source_id:
        LOG.info("categories data source auto-discovered from books relation")
    authors_pages, authors_title_key = _optional_pages(
        api,
        authors_data_source_id,
        "authors",
        strict=bool(config.authors_data_source_id),
    )
    categories_pages, categories_title_key = _optional_pages(
        api,
        categories_data_source_id,
        "categories",
        strict=bool(config.categories_data_source_id),
    )
    author_lookup = _entity_map(authors_pages, authors_title_key)
    category_lookup = _entity_map(categories_pages, categories_title_key)

    books: list[dict[str, Any]] = []
    page_to_book: dict[str, dict[str, Any]] = {}
    taken_slugs: set[str] = set()
    for page in books_pages:
        book = _extract_book(
            page,
            title_key=books_title_key,
            author_lookup=author_lookup,
            category_lookup=category_lookup,
            taken_slugs=taken_slugs,
        )
        if not book:
            continue
        books.append(book)
        page_to_book[book["page_id"]] = book

    chapter_pages, chapters_title_key = _optional_pages(
        api, config.chapters_data_source_id, "chapters"
    )
    chapters = _chapter_lookup(chapter_pages, chapters_title_key)
    bookmark_pages: list[dict[str, Any]] = []
    bookmark_title_key: str | None = None
    if config.include_highlights:
        bookmark_pages, bookmark_title_key = _optional_pages(
            api, config.bookmarks_data_source_id, "bookmarks"
        )
    review_pages: list[dict[str, Any]] = []
    review_title_key: str | None = None
    if config.include_reviews:
        review_pages, review_title_key = _optional_pages(
            api, config.reviews_data_source_id, "reviews"
        )
    _attach_annotations(
        books,
        page_to_book,
        bookmark_pages,
        review_pages,
        chapters,
        config,
        bookmark_title_key,
        review_title_key,
    )

    daily_pages, daily_title_key = _optional_pages(
        api, config.daily_data_source_id, "daily reading"
    )
    reading_log_data_source_id, reading_log_is_discovered = (
        _select_reading_log_data_source(
            books_data_source,
            config.reading_log_data_source_id,
        )
    )
    if reading_log_is_discovered:
        LOG.info("reading log data source auto-discovered from books relation")
    elif reading_log_data_source_id:
        LOG.info("reading log data source loaded from configuration")

    reading_log_pages, reading_log_title_key = _optional_pages(
        api,
        reading_log_data_source_id,
        "reading log",
        strict=bool(config.reading_log_data_source_id),
    )
    if not daily_pages and reading_log_pages:
        LOG.info("using reading log as the daily reading source")
        daily_pages, daily_title_key = reading_log_pages, reading_log_title_key
    daily = normalize_daily(daily_pages, daily_title_key)
    book_lookup = dict(page_to_book)
    for book in books:
        source_id = as_text(book.get("source_id"))
        if source_id:
            book_lookup[source_id] = book
    book_daily_records = _attach_book_daily(
        books,
        book_lookup,
        reading_log_pages or [],
        reading_log_title_key,
    )
    if book_daily_records:
        LOG.info(
            "attached per-book daily reading to %d books (%d book-days)",
            sum(1 for book in books if book.get("reading_daily")),
            book_daily_records,
        )

    books.sort(
        key=lambda item: (
            item.get("last_read_at") or "",
            item.get("finished_at") or "",
            item.get("title") or "",
        ),
        reverse=True,
    )
    for book in books:
        book.pop("page_id", None)
    stats = compute_stats(books, daily)
    authors = aggregate_entities(books, "authors")
    categories = aggregate_entities(books, "categories")
    return {
        "schema_version": 1,
        "generated_at": iso_now(),
        "site": {
            "title": config.site_title,
            "description": config.site_description,
            "url": config.site_url,
        },
        "meta": {
            "source": "notion",
            "books": len(books),
            "highlights": sum(int(book.get("highlight_count") or 0) for book in books),
            "notes": sum(int(book.get("note_count") or 0) for book in books),
            "daily_records": len(daily),
            "book_daily_records": book_daily_records,
            "include_highlights": config.include_highlights,
            "include_reviews": config.include_reviews,
        },
        "books": books,
        "authors": authors,
        "categories": categories,
        "daily": daily,
        "stats": stats,
    }


def write_snapshot(snapshot: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(output)
    LOG.info("wrote %s (%.1f KB)", output, output.stat().st_size / 1024)


def _comparable_snapshot(snapshot: dict[str, Any]) -> str:
    comparable = copy.deepcopy(snapshot)
    comparable.pop("generated_at", None)
    stats = comparable.get("stats")
    if isinstance(stats, dict):
        # The rolling heatmap moves with the calendar. The site builder can
        # recompute it even when the source data hasn't changed.
        stats.pop("heatmap", None)
    return json.dumps(
        comparable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def snapshots_equivalent(
    existing: dict[str, Any], incoming: dict[str, Any]
) -> bool:
    return _comparable_snapshot(existing) == _comparable_snapshot(incoming)


def write_snapshot_if_changed(snapshot: dict[str, Any], output: Path) -> bool:
    if output.exists():
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
        if isinstance(existing, dict) and snapshots_equivalent(existing, snapshot):
            LOG.info("snapshot unchanged; preserving %s", output)
            return False
    write_snapshot(snapshot, output)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "public-snapshot.json",
        help="write the sanitized public snapshot here",
    )
    parser.add_argument("--site-title", default=None)
    parser.add_argument("--site-url", default=None)
    parser.add_argument("--site-description", default=None)
    parser.add_argument("--include-reviews", action="store_true")
    parser.add_argument("--no-highlights", action="store_true")
    parser.add_argument("--max-highlights", type=int, default=None)
    parser.add_argument("--max-highlight-chars", type=int, default=None)
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    config = config_from_env()
    if args.site_title:
        config.site_title = args.site_title
    if args.site_url:
        config.site_url = args.site_url.rstrip("/")
    if args.site_description:
        config.site_description = args.site_description
    if args.include_reviews:
        config.include_reviews = True
    if args.no_highlights:
        config.include_highlights = False
    if args.max_highlights is not None:
        config.max_highlights_per_book = args.max_highlights
    if args.max_highlight_chars is not None:
        config.max_highlight_chars = args.max_highlight_chars
    try:
        snapshot = build_snapshot(config)
    except NotionAPIError as error:
        LOG.error("%s", error)
        return 2
    changed = write_snapshot_if_changed(snapshot, args.output)
    if args.print_summary:
        print(
            json.dumps(
                {**snapshot["meta"], "snapshot_changed": changed},
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
