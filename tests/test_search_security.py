from __future__ import annotations

import requests


def test_brave_search_hides_internal_network_errors(monkeypatch):
    from core import jarvis_config
    from tools import search

    def _raise_proxy_error(*args, **kwargs):
        raise requests.exceptions.ProxyError("proxyerror host=127.0.0.1 port=9 secret-ish detail")

    monkeypatch.setattr(jarvis_config, "BRAVE_API_KEY", "test-key")
    monkeypatch.setattr(search.http_requests, "get", _raise_proxy_error)
    monkeypatch.setattr("time.sleep", lambda *args, **kwargs: None)

    result = search._buscar_en_brave("precio actual del bitcoin").lower()

    assert "unavailable" in result or "no está disponible" in result
    assert "proxyerror" not in result
    assert "127.0.0.1" not in result
    assert "secret-ish" not in result


def test_brave_search_hides_provider_error_body(monkeypatch):
    from core import jarvis_config
    from tools import search

    class Response:
        status_code = 403
        text = "token=secret-value internal-provider-detail"

    monkeypatch.setattr(jarvis_config, "BRAVE_API_KEY", "test-key")
    monkeypatch.setattr(search.http_requests, "get", lambda *args, **kwargs: Response())

    result = search._buscar_en_brave("current topic").lower()

    assert "403" in result
    assert "secret-value" not in result
    assert "internal-provider-detail" not in result


def test_brave_search_cleans_html_from_results(monkeypatch):
    from core import jarvis_config
    from tools import search

    class Response:
        status_code = 200

        def json(self):
            return {
                "web": {
                    "results": [
                        {
                            "title": "Python &amp; Security",
                            "description": "Use <strong>safe</strong> defaults&#x27; now.",
                            "url": "https://example.test/result",
                        }
                    ]
                }
            }

    monkeypatch.setattr(jarvis_config, "BRAVE_API_KEY", "test-key")
    monkeypatch.setattr(search.http_requests, "get", lambda *args, **kwargs: Response())

    result = search._buscar_en_brave("Python security")

    assert "Python & Security" in result
    assert "safe defaults' now" in result
    assert "<strong>" not in result
    assert "&#x27;" not in result
