import json
import shutil
from pathlib import Path

from weread_site.build import BuildConfig, build_site
from weread_site.demo import demo_snapshot


def test_demo_site_builds_expected_pages(tmp_path: Path):
    app_root = Path(__file__).resolve().parents[1]
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(demo_snapshot(), ensure_ascii=False), encoding="utf-8"
    )
    output = tmp_path / "site"
    build_site(
        BuildConfig(
            snapshot=snapshot,
            output=output,
            download_covers=False,
            cover_cache=tmp_path / "covers",
        )
    )
    assert (output / "index.html").exists()
    assert (output / "books" / "index.html").exists()
    assert (output / "stats" / "index.html").exists()
    assert (output / "search" / "index.html").exists()
    assert (output / "data" / "search.json").exists()
    assert (output / "sitemap.xml").exists()
    assert (output / ".nojekyll").exists()
    book_pages = list((output / "book").glob("*/index.html"))
    assert len(book_pages) == len(demo_snapshot()["books"])
    home = (output / "index.html").read_text(encoding="utf-8")
    assert "读书记" in home
    assert "最近在读" in home
    assert "年度阅读" in home
    assert "/data/search.json" in (output / "search" / "index.html").read_text(
        encoding="utf-8"
    )


def test_base_path_is_applied(tmp_path: Path):
    app_root = Path(__file__).resolve().parents[1]
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(demo_snapshot(), ensure_ascii=False), encoding="utf-8"
    )
    output = tmp_path / "site"
    build_site(
        BuildConfig(
            snapshot=snapshot,
            output=output,
            base_path="/reading",
            site_url="https://example.com",
            download_covers=False,
            cover_cache=tmp_path / "covers",
        )
    )
    home = (output / "index.html").read_text(encoding="utf-8")
    assert 'href="/reading/books/"' in home
    assert "<loc>https://example.com/reading/</loc>" in (
        output / "sitemap.xml"
    ).read_text(encoding="utf-8")


def test_sidebar_toggle_is_accessible_and_persistent(tmp_path: Path):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(demo_snapshot(), ensure_ascii=False), encoding="utf-8"
    )
    output = tmp_path / "site"
    build_site(
        BuildConfig(
            snapshot=snapshot,
            output=output,
            download_covers=False,
            cover_cache=tmp_path / "covers",
        )
    )

    home = (output / "index.html").read_text(encoding="utf-8")
    assert 'id="menu-button"' in home
    assert 'aria-controls="sidebar"' in home
    assert 'aria-expanded="false"' in home
    assert "weread.sidebar" in home

    css = next((output / "assets").glob("style.*.css")).read_text(encoding="utf-8")
    script = next((output / "assets").glob("app.*.js")).read_text(encoding="utf-8")
    assert "sidebar-collapsed" in css
    assert "sidebar-collapsed" in script
    assert "weread.sidebar" in script


def test_stats_page_has_year_switch_and_custom_heatmap_tooltips(tmp_path: Path):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(demo_snapshot(), ensure_ascii=False), encoding="utf-8"
    )
    output = tmp_path / "site"
    build_site(
        BuildConfig(
            snapshot=snapshot,
            output=output,
            download_covers=False,
            cover_cache=tmp_path / "covers",
        )
    )

    stats = (output / "stats" / "index.html").read_text(encoding="utf-8")
    assert 'id="heatmap-year"' in stats
    assert 'id="heatmap-tooltip"' in stats
    assert stats.count('data-heatmap-panel="') >= 2
    assert "data-heatmap-cell" in stats
    assert "悬停或聚焦方块" in stats

    css = next((output / "assets").glob("style.*.css")).read_text(encoding="utf-8")
    script = next((output / "assets").glob("app.*.js")).read_text(encoding="utf-8")
    assert "--heat-4" in css
    assert ".heatmap-tooltip" in css
    assert "weread.heatmap-year" in script
    assert 'event.key === "ArrowLeft"' in script


def test_rebuild_cleans_stale_output_and_uses_absolute_og_images(tmp_path: Path):
    app_root = Path(__file__).resolve().parents[1]
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(demo_snapshot(), ensure_ascii=False), encoding="utf-8"
    )
    output = tmp_path / "site"
    config = BuildConfig(
        snapshot=snapshot,
        output=output,
        site_url="https://example.com",
        download_covers=False,
        cover_cache=tmp_path / "covers",
    )
    build_site(config)
    stale = output / "assets" / "stale.js"
    stale.write_text("old", encoding="utf-8")
    build_site(config)
    assert not stale.exists()

    book_page = next((output / "book").glob("*/index.html"))
    page = book_page.read_text(encoding="utf-8")
    assert '<meta property="og:image" content="https://example.com/assets/covers/' in page
    assert '"image": "https://example.com/assets/covers/' in page
