"""Small, dependency-light Notion API client for read-only snapshots."""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import unquote

import requests


LOG = logging.getLogger("weread-reading.notion")


class NotionAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def normalize_notion_id(value: str) -> str:
    """Accept a raw Notion ID or a copied Notion URL."""
    text = unquote(str(value or "")).strip()
    match = re.search(
        r"(?<![0-9a-fA-F])("
        r"[0-9a-fA-F]{32}"
        r"|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
        r")(?![0-9a-fA-F])",
        text,
    )
    return match.group(1) if match else text


class NotionAPI:
    def __init__(
        self,
        token: str,
        *,
        version: str = "2025-09-03",
        request_interval: float = 0.34,
        timeout: float = 30.0,
        max_retries: int = 6,
    ) -> None:
        if not token:
            raise NotionAPIError("NOTION_TOKEN is required")
        self.token = token
        self.version = version
        self.request_interval = max(0.0, request_interval)
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self._last_request_at = 0.0
        self._data_source_cache: dict[str, str] = {}

    def _wait_for_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.request_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"https://api.notion.com/v1/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.version,
            "Content-Type": "application/json",
        }
        for attempt in range(1, self.max_retries + 1):
            self._wait_for_rate_limit()
            try:
                response = self.session.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    timeout=self.timeout,
                )
            except requests.RequestException as error:
                if attempt >= self.max_retries:
                    raise NotionAPIError(f"Notion request failed: {error}") from error
                delay = min(30, 2 ** (attempt - 1))
                LOG.warning("Notion network error, retrying in %ss: %s", delay, error)
                time.sleep(delay)
                continue
            finally:
                self._last_request_at = time.monotonic()

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else 2 ** attempt
                except ValueError:
                    delay = 2 ** attempt
                delay = min(60.0, max(1.0, delay))
                LOG.warning("Notion rate limited, retrying in %ss", delay)
                time.sleep(delay)
                continue
            if 500 <= response.status_code < 600:
                if attempt >= self.max_retries:
                    raise NotionAPIError(
                        f"Notion server error {response.status_code}: {response.text[:300]}",
                        response.status_code,
                    )
                delay = min(30, 2 ** (attempt - 1))
                LOG.warning("Notion server error %s, retrying in %ss", response.status_code, delay)
                time.sleep(delay)
                continue
            if response.status_code >= 400:
                message = response.text[:500]
                raise NotionAPIError(
                    f"Notion API {response.status_code} for {method} {path}: {message}",
                    response.status_code,
                )
            try:
                return response.json()
            except ValueError as error:
                raise NotionAPIError(
                    f"Notion returned non-JSON for {method} {path}"
                ) from error
        raise NotionAPIError(f"Notion request exhausted retries: {method} {path}")

    def retrieve_data_source(self, data_source_id: str) -> dict[str, Any]:
        return self.request(
            "GET", f"data_sources/{normalize_notion_id(data_source_id)}"
        )

    def retrieve_database(self, database_id: str) -> dict[str, Any]:
        return self.request("GET", f"databases/{normalize_notion_id(database_id)}")

    def resolve_data_source_id(self, identifier: str) -> str:
        """Resolve either a data source ID or a database ID to a data source ID."""
        raw = normalize_notion_id(identifier)
        if not raw:
            raise NotionAPIError("Notion data source or database ID is empty")
        if raw in self._data_source_cache:
            return self._data_source_cache[raw]

        try:
            data_source = self.retrieve_data_source(raw)
        except NotionAPIError as error:
            if error.status_code not in {400, 404}:
                raise
            try:
                database = self.retrieve_database(raw)
            except NotionAPIError as database_error:
                raise NotionAPIError(
                    "Notion ID is neither a readable data source nor database: "
                    f"{raw} ({error}; {database_error})",
                    database_error.status_code or error.status_code,
                ) from database_error
            candidates = database.get("data_sources") or []
            initial = database.get("initial_data_source") or {}
            resolved = (
                (candidates[0] or {}).get("id")
                if candidates and isinstance(candidates[0], dict)
                else initial.get("id")
            )
            if not resolved:
                raise NotionAPIError(
                    f"Notion database {raw} does not expose a data source"
                )
        else:
            resolved = str(data_source.get("id") or raw)

        resolved = normalize_notion_id(str(resolved))
        self._data_source_cache[raw] = resolved
        return resolved

    def query_all(
        self,
        data_source_id: str,
        *,
        page_size: int = 100,
        filter_body: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        data_source_id = self.resolve_data_source_id(data_source_id)
        results: list[dict[str, Any]] = []
        start_cursor: str | None = None
        while True:
            body: dict[str, Any] = {"page_size": page_size}
            if start_cursor:
                body["start_cursor"] = start_cursor
            if filter_body:
                body["filter"] = filter_body
            response = self.request(
                "POST", f"data_sources/{data_source_id}/query", json_body=body
            )
            page = response.get("results") or []
            results.extend(page)
            LOG.info("  fetched %s rows from %s", len(results), data_source_id[:8])
            if not response.get("has_more"):
                break
            start_cursor = response.get("next_cursor")
            if not start_cursor:
                break
        return results


def find_title_key(data_source: dict[str, Any]) -> str | None:
    for key, value in (data_source.get("properties") or {}).items():
        if isinstance(value, dict) and value.get("type") == "title":
            return key
    return None


def page_icon_url(page: dict[str, Any]) -> str | None:
    icon = page.get("icon")
    if not isinstance(icon, dict):
        return None
    if icon.get("type") == "external":
        return (icon.get("external") or {}).get("url")
    if icon.get("type") == "file":
        return (icon.get("file") or {}).get("url")
    return None
