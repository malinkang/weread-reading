from datetime import date

from weread_site.model import (
    aggregate_entities,
    compute_heatmap,
    compute_stats,
    compute_year_heatmaps,
    make_search_index,
    normalize_my_rating,
    normalize_progress,
    normalize_public_rating,
    normalize_status,
    parse_date,
    property_value,
    reading_time_label,
    stable_slug,
)


def test_property_value_handles_common_notion_types():
    assert property_value(
        {"type": "title", "title": [{"plain_text": "三体"}]}
    ) == "三体"
    assert property_value(
        {"type": "rich_text", "rich_text": [{"plain_text": "刘慈欣"}]}
    ) == "刘慈欣"
    assert property_value({"type": "status", "status": {"name": "读完"}}) == "读完"
    assert property_value({"type": "number", "number": 42}) == 42
    assert property_value(
        {"type": "relation", "relation": [{"id": "page-1"}, {"id": "page-2"}]}
    ) == ["page-1", "page-2"]
    assert property_value(
        {
            "type": "files",
            "files": [{"type": "external", "external": {"url": "https://cover/x.jpg"}}],
        }
    ) == ["https://cover/x.jpg"]


def test_date_and_rating_normalization():
    assert parse_date("2026-11-12") == date(2026, 11, 12)
    assert parse_date("2026") == date(2026, 1, 1)
    assert parse_date(2026) == date(2026, 1, 1)
    assert normalize_my_rating("⭐⭐⭐⭐") == 4
    assert normalize_my_rating(3) == 3
    assert normalize_public_rating(93) == 9.3
    assert normalize_public_rating(9.1) == 9.1
    assert normalize_progress(62) == 0.62
    assert normalize_progress(0.4) == 0.4


def test_status_and_duration_labels():
    assert normalize_status("已读") == "finished"
    assert normalize_status("在读") == "reading"
    assert normalize_status("想读") == "want"
    assert normalize_status(None, progress=1) == "finished"
    assert reading_time_label(3661) == "1 小时 1 分钟"
    assert reading_time_label(0) == "未记录"


def test_slug_is_stable_and_unique():
    first = stable_slug("三体", "book-1")
    second = stable_slug("三体", "book-2")
    assert first.startswith("san-ti-")
    assert first != second


def test_stats_and_entities():
    books = [
        {
            "slug": "a",
            "title": "A",
            "authors": ["Author"],
            "categories": ["Tech"],
            "status": "finished",
            "reading_seconds": 3600,
            "highlight_count": 2,
            "note_count": 1,
            "words": 1000,
            "finished_at": "2026-01-02",
        },
        {
            "slug": "b",
            "title": "B",
            "authors": ["Author"],
            "categories": ["Tech", "Life"],
            "status": "reading",
            "reading_seconds": 1800,
            "highlight_count": 1,
            "note_count": 0,
            "words": 2000,
            "finished_at": None,
        },
    ]
    stats = compute_stats(books, [{"date": "2026-01-02", "seconds": 5400}])
    assert stats["books"] == 2
    assert stats["finished"] == 1
    assert stats["reading_seconds"] == 5400
    assert stats["highlights"] == 3
    assert stats["by_year"][0]["seconds"] == 5400
    authors = aggregate_entities(books, "authors")
    assert authors[0]["name"] == "Author"
    assert authors[0]["books"] == 2
    index = make_search_index(books)
    assert index[0]["slug"] == "a"


def test_heatmap_has_53_weeks():
    cells = compute_heatmap([{"date": "2026-11-12", "seconds": 3600}])
    assert len(cells) == 371
    assert any(cell["seconds"] == 3600 for cell in cells)


def test_year_heatmaps_support_switching_and_summarize_streaks():
    years = compute_year_heatmaps(
        [
            {"date": "2025-12-31", "seconds": 1800},
            {"date": "2026-01-01", "seconds": 3600},
            {"date": "2026-01-02", "seconds": 7200},
        ],
        today=date(2026, 1, 3),
    )
    assert [item["year"] for item in years] == [2025, 2026]
    current = years[-1]
    assert current["active_days"] == 2
    assert current["longest_streak"] == 2
    assert current["reading_time"] == "3 小时"
    assert all(len(week["cells"]) == 7 for week in current["weeks"])
    assert current["weeks"][0]["month_label"] == "1月"
    assert any(
        cell and cell["date"] == "2026-01-04" and cell["future"]
        for week in current["weeks"]
        for cell in week["cells"]
    )
