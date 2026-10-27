from __future__ import annotations

from tools import utilities


def test_leer_archivo_confines_reads_to_configured_roots(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    allowed_file = root / "notes.txt"
    denied_file = outside / "secret.txt"
    allowed_file.write_text("allowed content", encoding="utf-8")
    denied_file.write_text("outside content", encoding="utf-8")
    monkeypatch.setenv("JARVIS_DOCUMENT_ROOTS", str(root))

    allowed = utilities.leer_archivo.invoke({"nombre_archivo": "notes.txt"})
    denied = utilities.leer_archivo.invoke({"nombre_archivo": str(denied_file)})

    assert "allowed content" in allowed
    assert "outside content" not in denied
    assert "No encontré" in denied


def test_leer_archivo_accepts_an_absolute_path_inside_a_root(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    target = root / "nested" / "note.txt"
    target.parent.mkdir()
    target.write_text("nested content", encoding="utf-8")
    monkeypatch.setenv("JARVIS_DOCUMENT_ROOTS", str(root))

    result = utilities.leer_archivo.invoke({"nombre_archivo": str(target)})

    assert "nested content" in result
