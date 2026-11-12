import pytest

from weread_site.notion_api import (
    NotionAPI,
    NotionAPIError,
    normalize_notion_id,
)


def test_normalize_notion_id_accepts_database_url():
    value = (
        "https://www.notion.so/me/Reading-"
        "0123456789abcdef0123456789abcdef?v=feedfacefeedfacefeedfacefeedface"
    )
    assert normalize_notion_id(value) == "0123456789abcdef0123456789abcdef"


def test_database_id_resolves_to_first_data_source():
    api = NotionAPI("test-token", request_interval=0)
    calls = []

    def fake_request(method, path, *, json_body=None):
        calls.append((method, path))
        if path.startswith("data_sources/"):
            raise NotionAPIError("not a data source", 404)
        assert path == "databases/database-id"
        return {"data_sources": [{"id": "data-source-id"}]}

    api.request = fake_request
    assert api.resolve_data_source_id("database-id") == "data-source-id"
    assert api.resolve_data_source_id("database-id") == "data-source-id"
    assert calls == [
        ("GET", "data_sources/database-id"),
        ("GET", "databases/database-id"),
    ]


def test_data_source_id_is_not_looked_up_as_database():
    api = NotionAPI("test-token", request_interval=0)
    calls = []

    def fake_request(method, path, *, json_body=None):
        calls.append(path)
        return {"id": "data-source-id"}

    api.request = fake_request
    assert api.resolve_data_source_id("data-source-id") == "data-source-id"
    assert calls == ["data_sources/data-source-id"]


def test_database_without_a_data_source_is_rejected():
    api = NotionAPI("test-token", request_interval=0)

    def fake_request(method, path, *, json_body=None):
        if path.startswith("data_sources/"):
            raise NotionAPIError("not a data source", 404)
        return {"data_sources": []}

    api.request = fake_request
    with pytest.raises(NotionAPIError, match="does not expose a data source"):
        api.resolve_data_source_id("database-id")
