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

    assert result == "i could not query the network at this time."
    assert "proxyerror" not in result
    assert "127.0.0.1" not in result
    assert "secret-ish" not in result
