from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIDGETS_JS = ROOT / "src" / "frontend" / "static" / "js" / "modules" / "widgets.js"
INDEX_HTML = ROOT / "src" / "frontend" / "templates" / "index.html"
BACKEND = ROOT / "src" / "backend" / "jarvis_backend.py"


def test_widgets_do_not_render_llm_payloads_with_inner_html():
    widgets_js = WIDGETS_JS.read_text(encoding="utf-8")

    assert ".innerHTML" not in widgets_js
    assert "textContent" in widgets_js
    assert "createElement" in widgets_js
    assert "safeImageSrc" in widgets_js


def test_csp_allows_https_images_for_spotify_artwork():
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    backend = BACKEND.read_text(encoding="utf-8")

    assert "img-src 'self' https: data: blob:" in index_html
    assert "img-src 'self' https: data: blob:" in backend
