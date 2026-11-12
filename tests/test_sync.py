from weread_site.sync import (
    SyncConfig,
    _attach_annotations,
    _attach_book_daily,
    _extract_book,
    build_snapshot,
    config_from_env,
    find_relation_data_source,
    snapshots_equivalent,
    write_snapshot_if_changed,
)


def _page(page_id, properties, icon=None):
    return {"id": page_id, "properties": properties, "icon": icon}


def _title(text):
    return {"type": "title", "title": [{"plain_text": text}]}


def _rich(text):
    return {"type": "rich_text", "rich_text": [{"plain_text": text}]}


def _relation(*ids):
    return {"type": "relation", "relation": [{"id": item} for item in ids]}


def _config(**overrides):
    values = {
        "token": "test-token",
        "books_data_source_id": "books",
        "include_highlights": True,
        "include_reviews": False,
        "max_highlights_per_book": 2,
        "max_highlight_chars": 80,
    }
    values.update(overrides)
    return SyncConfig(**values)


def test_extract_book_maps_relations_and_public_fields():
    page = _page(
        "book-page-1",
        {
            "书名": _title("三体"),
            "BookId": _rich("weread-1"),
            "作者": _relation("author-1"),
            "分类": _relation("cat-1", "cat-2"),
            "阅读状态": {"type": "status", "status": {"name": "读完"}},
            "阅读进度": {"type": "number", "number": 100},
            "阅读时长": {"type": "number", "number": 3600},
            "阅读天数": {"type": "number", "number": 3},
            "我的评分": {"type": "select", "select": {"name": "⭐⭐⭐⭐⭐"}},
            "评分": {"type": "number", "number": 93},
            "封面": {
                "type": "files",
                "files": [{"type": "external", "external": {"url": "https://cover/1.jpg"}}],
            },
            "出版年份": {"type": "number", "number": 2008},
            "最后阅读时间": {
                "type": "date",
                "date": {"start": "2026-02-08"},
            },
        },
        icon={"type": "external", "external": {"url": "https://icon/1.jpg"}},
    )
    book = _extract_book(
        page,
        title_key="书名",
        author_lookup={"author-1": "刘慈欣"},
        category_lookup={"cat-1": "科幻", "cat-2": "文学"},
        taken_slugs=set(),
    )
    assert book is not None
    assert book["title"] == "三体"
    assert book["authors"] == ["刘慈欣"]
    assert book["categories"] == ["科幻", "文学"]
    assert book["status"] == "finished"
    assert book["progress"] == 1.0
    assert book["rating"] == 9.3
    assert book["my_rating"] == 5
    assert book["published"] == "2008"
    assert book["cover"] == "https://cover/1.jpg"
    assert book["finished_at"] == "2026-02-08"


def test_private_reviews_are_not_attached_by_default():
    book = {
        "page_id": "book-page-1",
        "slug": "san-ti",
        "highlight_count": 0,
        "note_count": 0,
        "highlights": [],
    }
    page_to_book = {"book-page-1": book}
    bookmark = _page(
        "bookmark-1",
        {
            "markText": _title("公开划线"),
            "书籍": _relation("book-page-1"),
            "chapterUid": {"type": "number", "number": 1},
        },
    )
    review = _page(
        "review-1",
        {
            "content": _title("私人想法"),
            "书籍": _relation("book-page-1"),
            "chapterUid": {"type": "number", "number": 1},
        },
    )
    _attach_annotations(
        [book],
        page_to_book,
        [bookmark],
        [review],
        {("book-page-1", "1"): "第一章"},
        _config(include_reviews=False),
    )
    assert book["highlight_count"] == 1
    assert book["note_count"] == 0
    assert [item["text"] for item in book["highlights"]] == ["公开划线"]
    assert "私人想法" not in str(book)


def test_reviews_can_be_opt_in_and_are_capped():
    book = {
        "page_id": "book-page-1",
        "slug": "san-ti",
        "highlight_count": 0,
        "note_count": 0,
        "highlights": [],
    }
    review = _page(
        "review-1",
        {
            "content": _title("私人想法"),
            "abstract": _rich("我的批注"),
            "书籍": _relation("book-page-1"),
            "chapterUid": {"type": "number", "number": 2},
        },
    )
    _attach_annotations(
        [book],
        {"book-page-1": book},
        [],
        [review],
        {("book-page-1", "2"): "第二章"},
        _config(include_reviews=True),
    )
    assert book["note_count"] == 1
    assert book["highlights"][0]["kind"] == "note"
    assert book["highlights"][0]["chapter"] == "第二章"
    assert book["highlights"][0]["note"] == "我的批注"


def test_relation_database_is_discovered_from_books_schema():
    data_source = {
        "properties": {
            "作者": {
                "type": "relation",
                "relation": {"database_id": "author-db"},
            },
            "分类": {
                "type": "relation",
                "relation": {"data_source_id": "category-ds"},
            },
        }
    }
    assert find_relation_data_source(data_source, ("作者", "Author")) == "author-db"
    assert find_relation_data_source(data_source, ("分类",)) == "category-ds"
    assert find_relation_data_source(data_source, ("书单",)) is None


def test_attach_book_daily_uses_book_relation_and_sums_duplicate_days():
    book = {
        "page_id": "book-page-1",
        "source_id": "weread-1",
        "reading_days": 0,
    }
    pages = [
        _page(
            "reading-log-1",
            {
                "书架": _relation("book-page-1"),
                "日期": {"type": "date", "date": {"start": "2026-01-02"}},
                "时长": {"type": "number", "number": 1200},
            },
        ),
        _page(
            "reading-log-2",
            {
                "书架": _relation("book-page-1"),
                "日期": {"type": "date", "date": {"start": "2026-01-02"}},
                "时长": {"type": "number", "number": 600},
            },
        ),
        _page(
            "reading-log-3",
            {
                "书架": _relation("book-page-1"),
                "日期": {"type": "date", "date": {"start": "2026-01-03"}},
                "时长": {"type": "number", "number": 900},
            },
        ),
    ]
    records = _attach_book_daily(
        [book],
        {"book-page-1": book},
        pages,
    )
    assert records == 2
    assert book["reading_days"] == 2
    assert book["reading_daily"] == [
        {"date": "2026-01-02", "seconds": 1800},
        {"date": "2026-01-03", "seconds": 900},
    ]


def test_database_id_aliases_and_empty_site_description(monkeypatch):
    for name in (
        "WEREAD_BOOKS_DATA_SOURCE_ID",
        "WEREAD_BOOKS_DATABASE_ID",
        "BOOKSHELF_DATA_SOURCE_ID",
        "NOTION_DATABASE_ID",
        "PUBLIC_SITE_DESCRIPTION",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WEREAD_BOOKS_DATABASE_ID", "database-id")
    monkeypatch.setenv("PUBLIC_SITE_DESCRIPTION", "")
    config = config_from_env()
    assert config.books_data_source_id == "database-id"
    assert config.site_description == "我的微信读书书架，按阅读时间、评分和划线整理。"


def test_snapshot_comparison_ignores_generated_time_and_rolling_heatmap():
    base = {
        "generated_at": "2026-11-12T10:00:00+08:00",
        "books": [{"title": "三体"}],
        "stats": {"heatmap": [{"date": "2026-11-12", "seconds": 10}]},
    }
    later = {
        "generated_at": "2026-11-13T10:00:00+08:00",
        "books": [{"title": "三体"}],
        "stats": {"heatmap": [{"date": "2026-11-13", "seconds": 20}]},
    }
    assert snapshots_equivalent(base, later)
    later["books"][0]["title"] = "三体 II"
    assert not snapshots_equivalent(base, later)


def test_write_snapshot_skips_unchanged_content(tmp_path):
    output = tmp_path / "snapshot.json"
    first = {
        "generated_at": "2026-11-12T10:00:00+08:00",
        "books": [{"title": "三体"}],
        "stats": {"heatmap": [{"date": "2026-11-12"}]},
    }
    assert write_snapshot_if_changed(first, output) is True
    original = output.read_text(encoding="utf-8")
    second = {
        "generated_at": "2026-11-13T10:00:00+08:00",
        "books": [{"title": "三体"}],
        "stats": {"heatmap": [{"date": "2026-11-13"}]},
    }
    assert write_snapshot_if_changed(second, output) is False
    assert output.read_text(encoding="utf-8") == original


def test_extract_book_handles_string_publication_date_and_skips_non_books():
    page = _page(
        "article-1",
        {
            "书名": _title("一篇文章"),
            "类型": {"type": "select", "select": {"name": "文章"}},
        },
    )
    assert (
        _extract_book(
            page,
            title_key="书名",
            author_lookup={},
            category_lookup={},
            taken_slugs=set(),
        )
        is None
    )
    book = _extract_book(
        _page(
            "book-1",
            {
                "书名": _title("活着"),
                "类型": {"type": "select", "select": {"name": "书籍"}},
                "出版日期": {"type": "date", "date": {"start": "1993-01-01"}},
            },
        ),
        title_key="书名",
        author_lookup={},
        category_lookup={},
        taken_slugs=set(),
    )
    assert book is not None
    assert book["published"] == "1993-01-01"


def test_build_snapshot_accepts_books_database_id_and_discovers_relations():
    class FakeAPI:
        ids = {
            "book-db": "book-ds",
            "author-db": "author-ds",
            "category-db": "category-ds",
            "reading-log-db": "reading-log-ds",
        }
        sources = {
            "book-ds": {
                "id": "book-ds",
                "properties": {
                    "书名": {"type": "title"},
                    "作者": {
                        "type": "relation",
                        "relation": {"database_id": "author-db"},
                    },
                    "分类": {
                        "type": "relation",
                        "relation": {"database_id": "category-db"},
                    },
                },
            },
            "author-ds": {
                "id": "author-ds",
                "properties": {"Name": {"type": "title"}},
            },
            "category-ds": {
                "id": "category-ds",
                "properties": {"Name": {"type": "title"}},
            },
            "reading-log-ds": {
                "id": "reading-log-ds",
                "properties": {
                    "标题": {"type": "title"},
                    "日期": {"type": "date"},
                    "时长": {"type": "number"},
                    "书架": {"type": "relation"},
                },
            },
        }
        rows = {
            "book-db": [
                _page(
                    "book-page",
                    {
                        "书名": _title("三体"),
                        "BookId": _rich("weread-1"),
                        "作者": _relation("author-page"),
                        "分类": _relation("category-page"),
                    },
                )
            ],
            "author-db": [_page("author-page", {"Name": _title("刘慈欣")})],
            "category-db": [_page("category-page", {"Name": _title("科幻")})],
            "reading-log-db": [
                _page(
                    "reading-log-1",
                    {
                        "标题": _title("2026年01月02日"),
                        "日期": {
                            "type": "date",
                            "date": {"start": "2026-01-02"},
                        },
                        "时长": {"type": "number", "number": 1800},
                        "书架": _relation("book-page"),
                    },
                )
            ],
        }

        def resolve_data_source_id(self, identifier):
            return self.ids.get(identifier, identifier)

        def retrieve_data_source(self, data_source_id):
            return self.sources[data_source_id]

        def query_all(self, data_source_id, **kwargs):
            return self.rows[data_source_id]

    snapshot = build_snapshot(
        _config(
            books_data_source_id="book-db",
            authors_data_source_id=None,
            categories_data_source_id=None,
            reading_log_data_source_id="reading-log-db",
        ),
        api=FakeAPI(),
    )
    assert snapshot["books"][0]["title"] == "三体"
    assert snapshot["books"][0]["authors"] == ["刘慈欣"]
    assert snapshot["books"][0]["categories"] == ["科幻"]
    assert snapshot["books"][0]["reading_daily"] == [
        {"date": "2026-01-02", "seconds": 1800}
    ]
    assert snapshot["books"][0]["reading_days"] == 1
    assert "page_id" not in snapshot["books"][0]
    assert "book-page" not in str(snapshot)
