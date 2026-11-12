"""Render the public WeRead snapshot as a fast static site."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import mimetypes
import os
import shutil
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .model import (
    STATUS_LABELS,
    aggregate_entities,
    as_number,
    compute_stats,
    compute_year_heatmaps,
    date_text,
    make_search_index,
    normalize_daily,
    reading_time_compact,
    reading_time_label,
    slugify,
    truncate,
)


LOG = logging.getLogger("weread-reading.build")
APP_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class BuildConfig:
    snapshot: Path
    output: Path
    base_path: str = ""
    site_url: str = ""
    download_covers: bool = True
    cover_cache: Path = APP_ROOT / ".cache" / "covers"
    templates: Path = APP_ROOT / "templates"
    static: Path = APP_ROOT / "static"


def _load_snapshot(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("books"), list):
        raise ValueError(f"{path} is not a reading snapshot")
    data.setdefault("site", {})
    data.setdefault("authors", [])
    data.setdefault("categories", [])
    data.setdefault("daily", [])
    data.setdefault("stats", {})
    return data


def _base_path(value: str) -> str:
    value = (value or "").strip()
    if not value or value == "/":
        return ""
    return "/" + value.strip("/")


def _copy_hashed(source: Path, output_dir: Path, output_root: Path) -> str:
    content = source.read_bytes()
    digest = hashlib.sha1(content).hexdigest()[:10]
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{source.stem}.{digest}{source.suffix}"
    destination.write_bytes(content)
    return "/" + destination.relative_to(output_root).as_posix()


def _svg_cover(book: dict[str, Any]) -> str:
    palettes = [
        ("#24483f", "#d7a84a"),
        ("#432d3b", "#d77a61"),
        ("#243b53", "#d9b26f"),
        ("#3b2f2f", "#b7c9a8"),
        ("#2e3a46", "#e0a458"),
        ("#3d2b1f", "#c6a87c"),
    ]
    digest = int(hashlib.sha1(str(book.get("slug")).encode()).hexdigest()[:8], 16)
    background, accent = palettes[digest % len(palettes)]
    title = truncate(book.get("title") or "未命名", 60)
    authors = " / ".join(book.get("authors") or [])[:42]
    lines = textwrap.wrap(title, width=9, break_long_words=True)[:5] or ["未命名"]
    title_svg = "".join(
        f'<text x="52" y="{235 + index * 72}" class="title">{html.escape(line)}</text>'
        for index, line in enumerate(lines)
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 900" role="img" aria-label="{html.escape(title)}">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{background}"/>
      <stop offset="1" stop-color="#101820"/>
    </linearGradient>
  </defs>
  <rect width="600" height="900" fill="url(#g)"/>
  <rect x="26" y="26" width="548" height="848" fill="none" stroke="{accent}" stroke-opacity=".45" stroke-width="2"/>
  <circle cx="505" cy="110" r="42" fill="{accent}" fill-opacity=".16"/>
  <text x="52" y="112" class="kicker">WEREAD / PRIVATE LIBRARY</text>
  {title_svg}
  <line x1="52" y1="655" x2="180" y2="655" stroke="{accent}" stroke-width="3"/>
  <text x="52" y="712" class="author">{html.escape(authors or "未知作者")}</text>
  <text x="52" y="824" class="foot">读书记 · 公开书架</text>
  <style>
    .kicker {{ fill: {accent}; font: 600 17px/1.2 "PingFang SC", "Noto Sans SC", sans-serif; letter-spacing: 2px; }}
    .title {{ fill: #fffaf0; font: 600 56px/1.2 "Songti SC", "Noto Serif SC", Georgia, serif; }}
    .author {{ fill: #e9ddc8; font: 400 25px/1.2 "PingFang SC", "Noto Sans SC", sans-serif; }}
    .foot {{ fill: #c9bda9; font: 400 17px/1.2 "PingFang SC", "Noto Sans SC", sans-serif; letter-spacing: 1px; }}
  </style>
</svg>
"""


def _cover_extension(content_type: str | None, url: str) -> str:
    if content_type:
        content_type = content_type.split(";", 1)[0].strip().lower()
        mapping = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
            "image/svg+xml": ".svg",
        }
        if content_type in mapping:
            return mapping[content_type]
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".jpg"


def _download_cover(book: dict[str, Any], cache_dir: Path) -> tuple[bytes, str] | None:
    url = book.get("cover")
    if not url:
        return None
    try:
        import requests
    except ImportError:
        return None
    try:
        response = requests.get(
            url,
            timeout=20,
            headers={"User-Agent": "weread-reading-site/0.1"},
        )
        response.raise_for_status()
        if not response.content:
            return None
        content_type = response.headers.get("Content-Type", "")
        if content_type and not content_type.startswith("image/"):
            return None
        extension = _cover_extension(content_type, url)
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{book['slug']}{extension}").write_bytes(response.content)
        return response.content, extension
    except Exception as error:
        LOG.debug("cover download failed for %s: %s", book.get("slug"), error)
        return None


def _prepare_cover(
    book: dict[str, Any], output_root: Path, cache_dir: Path, download: bool
) -> str:
    covers_dir = output_root / "assets" / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)
    slug = book["slug"]
    cached = sorted(cache_dir.glob(f"{slug}.*")) if cache_dir.exists() else []
    if cached:
        source = cached[0]
        destination = covers_dir / source.name
        if source.suffix != ".svg" and download:
            shutil.copyfile(source, destination)
            return f"/assets/covers/{destination.name}"
    if download:
        downloaded = _download_cover(book, cache_dir)
        if downloaded:
            content, extension = downloaded
            destination = covers_dir / f"{slug}{extension}"
            destination.write_bytes(content)
            return f"/assets/covers/{destination.name}"
    svg = _svg_cover(book)
    destination = covers_dir / f"{slug}.svg"
    destination.write_text(svg, encoding="utf-8")
    (cache_dir / f"{slug}.svg").parent.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{slug}.svg").write_text(svg, encoding="utf-8")
    return f"/assets/covers/{destination.name}"


def _prepare_covers(
    books: list[dict[str, Any]], output_root: Path, cache_dir: Path, download: bool
) -> None:
    workers = min(12, max(1, len(books)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_prepare_cover, book, output_root, cache_dir, download): book
            for book in books
        }
        for future in as_completed(futures):
            book = futures[future]
            try:
                book["cover_url"] = future.result()
            except Exception as error:
                LOG.warning("cover failed for %s: %s", book.get("slug"), error)
                book["cover_url"] = ""
    for book in books:
        book.setdefault("cover_url", "")


def _year_bars(stats: dict[str, Any]) -> list[dict[str, Any]]:
    years = list(stats.get("by_year") or [])
    maximum = max((int(item.get("seconds") or 0) for item in years), default=0)
    for item in years:
        item["width"] = (
            max(2.0, int(item.get("seconds") or 0) / maximum * 100.0)
            if maximum
            else 0.0
        )
    return years


def _category_bars(stats: dict[str, Any]) -> list[dict[str, Any]]:
    categories = list(stats.get("by_category") or [])[:10]
    maximum = max((int(item.get("seconds") or 0) for item in categories), default=0)
    for item in categories:
        item["width"] = (
            max(2.0, int(item.get("seconds") or 0) / maximum * 100.0)
            if maximum
            else 0.0
        )
    return categories


def _related_books(book: dict[str, Any], books: list[dict[str, Any]]) -> list[dict[str, Any]]:
    categories = set(book.get("categories") or [])
    authors = set(book.get("authors") or [])
    scored: list[tuple[int, dict[str, Any]]] = []
    for other in books:
        if other.get("slug") == book.get("slug"):
            continue
        score = 0
        score += len(categories & set(other.get("categories") or [])) * 2
        score += len(authors & set(other.get("authors") or [])) * 3
        if score:
            scored.append((score, other))
    scored.sort(key=lambda item: (item[0], item[1].get("reading_seconds") or 0), reverse=True)
    return [item[1] for item in scored[:4]]


def _highlight_groups(book: dict[str, Any]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for item in book.get("highlights") or []:
        chapter = item.get("chapter") or "未分章"
        if current is None or current["chapter"] != chapter:
            current = {"chapter": chapter, "entries": []}
            groups.append(current)
        current["entries"].append(item)
    return groups


def _json_ld(
    book: dict[str, Any],
    site: dict[str, Any],
    absolute_url: str,
    image_url: str = "",
) -> str:
    data = {
        "@context": "https://schema.org",
        "@type": "Book",
        "name": book.get("title"),
        "author": [{"@type": "Person", "name": author} for author in book.get("authors") or []],
        "url": absolute_url,
        "image": image_url or book.get("cover_url") or "",
        "description": book.get("intro") or "",
    }
    if book.get("isbn"):
        data["isbn"] = book["isbn"]
    if book.get("published"):
        data["datePublished"] = book["published"][:10]
    if book.get("publisher"):
        data["publisher"] = {"@type": "Organization", "name": book["publisher"]}
    return json.dumps(data, ensure_ascii=False)


def _reset_output(output: Path, snapshot: Path) -> None:
    """Remove a prior generated site without ever deleting source data."""
    resolved = output.resolve()
    snapshot_path = snapshot.resolve()
    protected = {
        Path(resolved.anchor),
        Path.home().resolve(),
        Path.cwd().resolve(),
        Path(__file__).resolve().parents[1],
    }
    if resolved in protected:
        raise ValueError(f"refusing to clean unsafe output directory: {output}")
    if snapshot_path == resolved or snapshot_path.is_relative_to(resolved):
        raise ValueError(f"output directory contains the snapshot: {output}")
    if resolved.exists():
        if not resolved.is_dir():
            raise ValueError(f"output path is not a directory: {output}")
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)


class SiteBuilder:
    def __init__(self, config: BuildConfig) -> None:
        self.config = config
        self.snapshot = _load_snapshot(config.snapshot)
        self.output = config.output
        self.base_path = _base_path(config.base_path)
        self.site = dict(self.snapshot.get("site") or {})
        self.site_url = (config.site_url or self.site.get("url") or "").rstrip("/")
        self.site.setdefault("title", "读书记")
        self.site.setdefault("description", "我的微信读书书架。")
        self.env = Environment(
            loader=FileSystemLoader(str(config.templates)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.env.globals.update(
            url=self.url,
            absolute_url=self.absolute_url,
            format_duration=reading_time_label,
            status_label=lambda value: STATUS_LABELS.get(value, "想读"),
            stars=self.stars,
        )
        self.env.filters["duration"] = reading_time_label
        self.env.filters["duration_compact"] = reading_time_compact
        self.env.filters["short_date"] = lambda value: (value or "")[:10]
        self.env.filters["stars"] = self.stars
        self.env.filters["slug"] = slugify
        self.env.filters["compact"] = lambda value: (
            f"{int(as_number(value) or 0) / 10000:.1f} 万"
            if (as_number(value) or 0) >= 10000
            else f"{int(as_number(value) or 0):,}"
        )

    def url(self, path: str) -> str:
        if not path:
            return self.base_path or "/"
        if path.startswith(("http://", "https://", "#", "mailto:")):
            return path
        return f"{self.base_path}/{path.lstrip('/')}" if self.base_path else f"/{path.lstrip('/')}"

    def absolute_url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        base = self.site_url or "https://example.com"
        return f"{base}{self.url(path)}"

    @staticmethod
    def stars(value: Any) -> str:
        number = int(as_number(value) or 0)
        return "★" * max(0, min(5, number)) + "☆" * max(0, 5 - number)

    def _context(
        self,
        *,
        page: str,
        title: str,
        description: str,
        path: str,
        **extra: Any,
    ) -> dict[str, Any]:
        assets = getattr(self, "assets", {})
        return {
            "page": page,
            "base_path": self.base_path,
            "site": self.site,
            "assets": assets,
            "generated_at": self.snapshot.get("generated_at", ""),
            "page_title": title,
            "page_description": description,
            "canonical": self.absolute_url(path),
            "og_type": "website",
            "og_image": "",
            "books": self.snapshot.get("books") or [],
            "authors": self.snapshot.get("authors") or [],
            "categories": self.snapshot.get("categories") or [],
            "stats": self.snapshot.get("stats") or {},
            **extra,
        }

    def _write(self, relative: str, template: str, context: dict[str, Any]) -> None:
        destination = self.output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        html_text = self.env.get_template(template).render(**context)
        destination.write_text(html_text, encoding="utf-8")

    def _prepare_assets(self) -> None:
        assets_dir = self.output / "assets"
        self.assets = {
            "style": _copy_hashed(self.config.static / "style.css", assets_dir, self.output),
            "app": _copy_hashed(self.config.static / "app.js", assets_dir, self.output),
            "favicon": _copy_hashed(self.config.static / "favicon.svg", assets_dir, self.output),
        }

    def _prepare_books(self) -> list[dict[str, Any]]:
        books = list(self.snapshot.get("books") or [])
        for book in books:
            book.setdefault("title", "未命名")
            book.setdefault("authors", ["未知作者"])
            book.setdefault("categories", [])
            book.setdefault("status", "want")
            book.setdefault("status_label", STATUS_LABELS.get(book["status"], "想读"))
            book.setdefault("reading_time_label", reading_time_label(book.get("reading_seconds")))
            book["highlight_groups"] = _highlight_groups(book)
        _prepare_covers(
            books,
            self.output,
            self.config.cover_cache,
            self.config.download_covers,
        )
        books.sort(
            key=lambda item: (
                item.get("last_read_at") or "",
                item.get("finished_at") or "",
                item.get("title") or "",
            ),
            reverse=True,
        )
        return books

    def build(self) -> Path:
        _reset_output(self.output, self.config.snapshot)
        self._prepare_assets()
        books = self._prepare_books()
        self.snapshot["books"] = books
        self.snapshot["stats"] = compute_stats(books, list(self.snapshot.get("daily") or []))
        self.snapshot["authors"] = aggregate_entities(books, "authors")
        self.snapshot["categories"] = aggregate_entities(books, "categories")
        stats = self.snapshot["stats"]
        heatmap_years = compute_year_heatmaps(list(self.snapshot.get("daily") or []))
        heatmap_default_year = next(
            (
                item["year"]
                for item in reversed(heatmap_years)
                if item["active_days"]
            ),
            heatmap_years[-1]["year"] if heatmap_years else "",
        )
        recent = [book for book in books if book.get("status") == "reading"][:4]
        finished = [book for book in books if book.get("status") == "finished"][:8]
        want = [book for book in books if book.get("status") == "want"][:4]
        featured = books[:8]
        categories = self.snapshot["categories"]
        authors = self.snapshot["authors"]
        search_index = make_search_index(books)

        self._write(
            "index.html",
            "index.html",
            self._context(
                page="home",
                title=self.site["title"],
                description=self.site["description"],
                path="/",
                recent=recent,
                finished=finished,
                want=want,
                featured=featured,
                top_categories=categories[:14],
                top_authors=authors[:8],
                year_bars=_year_bars(stats),
            ),
        )
        self._write(
            "books/index.html",
            "books.html",
            self._context(
                page="books",
                title=f"全部书籍 · {self.site['title']}",
                description="按阅读状态、评分和阅读时长浏览全部书籍。",
                path="/books/",
                book_count=len(books),
                top_categories=categories,
            ),
        )
        for book in books:
            related = _related_books(book, books)
            path = f"/book/{book['slug']}/"
            self._write(
                f"book/{book['slug']}/index.html",
                "book.html",
                self._context(
                    page="book",
                    title=f"{book['title']} · {self.site['title']}",
                    description=book.get("intro") or f"《{book['title']}》的阅读记录与划线。",
                    path=path,
                    og_type="article",
                    og_image=self.absolute_url(book["cover_url"])
                    if book.get("cover_url")
                    else "",
                    book=book,
                    related=related,
                    json_ld=_json_ld(
                        book,
                        self.site,
                        self.absolute_url(path),
                        self.absolute_url(book["cover_url"])
                        if book.get("cover_url")
                        else "",
                    ),
                ),
            )
        for category in categories:
            path = f"/topic/{category['slug']}/"
            items = [
                book
                for book in books
                if category["name"] in (book.get("categories") or [])
            ]
            self._write(
                f"topic/{category['slug']}/index.html",
                "topic.html",
                self._context(
                    page="topic",
                    title=f"{category['name']} · {self.site['title']}",
                    description=f"{category['name']}主题下的阅读记录。",
                    path=path,
                    topic=category,
                    topic_books=items,
                ),
            )
        for author in authors:
            path = f"/author/{author['slug']}/"
            items = [
                book for book in books if author["name"] in (book.get("authors") or [])
            ]
            self._write(
                f"author/{author['slug']}/index.html",
                "author.html",
                self._context(
                    page="author",
                    title=f"{author['name']} · {self.site['title']}",
                    description=f"{author['name']}的作品阅读记录。",
                    path=path,
                    author=author,
                    author_books=items,
                ),
            )
        self._write(
            "stats/index.html",
            "stats.html",
            self._context(
                page="stats",
                title=f"阅读统计 · {self.site['title']}",
                description="阅读时长、年度趋势、主题分布和阅读热力图。",
                path="/stats/",
                year_bars=_year_bars(stats),
                category_bars=_category_bars(stats),
                heatmap_years=heatmap_years,
                heatmap_default_year=heatmap_default_year,
                top_books=stats.get("top_books") or [],
            ),
        )
        self._write(
            "search/index.html",
            "search.html",
            self._context(
                page="search",
                title=f"搜索 · {self.site['title']}",
                description="搜索书名、作者、主题。",
                path="/search/",
            ),
        )
        self._write(
            "about/index.html",
            "about.html",
            self._context(
                page="about",
                title=f"关于 · {self.site['title']}",
                description="这个书架的数据来源、更新方式和公开范围。",
                path="/about/",
            ),
        )
        self._write(
            "404.html",
            "404.html",
            self._context(
                page="not-found",
                title=f"页面不存在 · {self.site['title']}",
                description="没有找到这个页面。",
                path="/404.html",
            ),
        )
        (self.output / ".nojekyll").write_text("", encoding="utf-8")
        (self.output / "data").mkdir(exist_ok=True)
        (self.output / "data" / "search.json").write_text(
            json.dumps(search_index, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        self._write_sitemap(books, categories, authors)
        (self.output / "robots.txt").write_text(
            "User-agent: *\nAllow: /\nSitemap: "
            + self.absolute_url("/sitemap.xml")
            + "\n",
            encoding="utf-8",
        )
        (self.output / "manifest.webmanifest").write_text(
            json.dumps(
                {
                    "name": self.site["title"],
                    "short_name": self.site["title"],
                    "start_url": self.url("/"),
                    "display": "standalone",
                    "background_color": "#f7f3eb",
                    "theme_color": "#b6461b",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return self.output

    def _write_sitemap(
        self,
        books: list[dict[str, Any]],
        categories: list[dict[str, Any]],
        authors: list[dict[str, Any]],
    ) -> None:
        paths = ["/", "/books/", "/stats/", "/about/"]
        paths += [f"/book/{book['slug']}/" for book in books]
        paths += [f"/topic/{category['slug']}/" for category in categories]
        paths += [f"/author/{author['slug']}/" for author in authors]
        entries = "".join(
            f"<url><loc>{html.escape(self.absolute_url(path))}</loc></url>" for path in paths
        )
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + entries
            + "</urlset>"
        )
        (self.output / "sitemap.xml").write_text(xml, encoding="utf-8")


def build_site(config: BuildConfig) -> Path:
    return SiteBuilder(config).build()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=APP_ROOT / "data" / "public-snapshot.json",
    )
    parser.add_argument("--output", type=Path, default=APP_ROOT / "site")
    parser.add_argument("--base-path", default=os.getenv("BASE_PATH", ""))
    parser.add_argument("--site-url", default=os.getenv("SITE_URL", ""))
    parser.add_argument("--no-download-covers", action="store_true")
    parser.add_argument("--cover-cache", type=Path, default=APP_ROOT / ".cache" / "covers")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not args.snapshot.exists():
        LOG.error("snapshot not found: %s", args.snapshot)
        return 2
    output = build_site(
        BuildConfig(
            snapshot=args.snapshot,
            output=args.output,
            base_path=args.base_path,
            site_url=args.site_url,
            download_covers=not args.no_download_covers,
            cover_cache=args.cover_cache,
        )
    )
    pages = sum(1 for path in output.rglob("*.html"))
    LOG.info("built %s pages into %s", pages, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
