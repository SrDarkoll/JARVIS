import os
from pathlib import Path

import pytest
from tools import desktop_document_resolver as resolver


@pytest.fixture
def document_root(tmp_path, monkeypatch):
    root = tmp_path / "Documents"
    root.mkdir()
    monkeypatch.setenv("JARVIS_DOCUMENT_ROOTS", str(root))
    return root


@pytest.mark.parametrize("suffix", [".pptx", ".pdf", ".docx", ".xlsx", ".txt"])
def test_allowed_direct_document_name(document_root, suffix):
    target = document_root / f"Informe final{suffix}"
    target.touch()

    assert resolver.resolve_local_document(target.name) == target.resolve()


def test_unique_prefix_resolves_without_opening_or_reading(document_root, monkeypatch):
    target = document_root / "RA1 Presentaci\u00f3n.pptx"
    target.touch()

    def forbidden(*args, **kwargs):
        pytest.fail("Document resolution must not open or read files")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr(os, "startfile", forbidden, raising=False)

    assert resolver.resolve_local_document("RA1") == target.resolve()


def test_exact_stem_precedes_prefix(document_root):
    target = document_root / "RA1.pdf"
    target.touch()
    (document_root / "RA1 presentacion.pptx").touch()

    assert resolver.resolve_local_document("RA1") == target.resolve()


def test_exact_filename_disambiguates_document_types(document_root):
    target = document_root / "RA1.pdf"
    target.touch()
    (document_root / "RA1.pptx").touch()

    assert resolver.resolve_local_document("RA1.pdf") == target.resolve()


@pytest.mark.parametrize("names", [
    ["RA1 Primero.pptx", "RA1 Segundo.pdf"],
    ["RA1.pptx", "RA1.pdf"],
])
def test_ambiguous_prefix_or_stem_has_diagnostic(document_root, names):
    for name in names:
        (document_root / name).touch()

    with pytest.raises(resolver.DocumentResolutionError) as error:
        resolver.resolve_local_document("RA1")

    assert error.value.diagnostic_code == "document_ambiguous"
    assert error.value.args == ("document_ambiguous",)
    assert str(document_root) not in str(error.value)


@pytest.mark.parametrize("reverse_roots", [False, True])
def test_collisions_are_ambiguous_independent_of_root_priority(document_root, tmp_path, monkeypatch, reverse_roots):
    other = tmp_path / "Other"
    other.mkdir()
    (document_root / "RA1.pdf").touch()
    (other / "RA1.pdf").touch()
    roots = [document_root, other]
    if reverse_roots:
        roots.reverse()
    monkeypatch.setenv("JARVIS_DOCUMENT_ROOTS", os.pathsep.join(map(str, roots)))

    with pytest.raises(resolver.DocumentResolutionError, match="^document_ambiguous$"):
        resolver.resolve_local_document("RA1.pdf")


def test_exact_name_in_later_root_precedes_prefix(document_root, tmp_path, monkeypatch):
    other = tmp_path / "Other"
    other.mkdir()
    (document_root / "RA1 notes.txt").touch()
    target = other / "RA1.pdf"
    target.touch()
    monkeypatch.setenv("JARVIS_DOCUMENT_ROOTS", os.pathsep.join(map(str, [document_root, other])))

    assert resolver.resolve_local_document("RA1") == target.resolve()


def test_explicit_extension_does_not_fall_back_to_another_type(document_root):
    (document_root / "RA1.pptx").touch()

    assert resolver.resolve_local_document("RA1.pdf") is None


def test_missing_document_returns_none(document_root):
    assert resolver.resolve_local_document("Missing.pdf") is None


@pytest.mark.parametrize("query", ["Informe final.pdf", "INFORME FINAL.PDF", "Informe final"])
def test_names_are_case_insensitive_and_preserve_spaces(document_root, query):
    target = document_root / "Informe final.pdf"
    target.touch()

    assert resolver.resolve_local_document(query) == target.resolve()


def test_canonical_unicode_name_and_non_latin_prefix(document_root):
    accented = document_root / "Caf\u00e9 final.pdf"
    accented.touch()
    japanese = document_root / "\u7814\u7a76\u5831\u544a.docx"
    japanese.touch()

    assert resolver.resolve_local_document("Cafe\u0301 final.pdf") == accented.resolve()
    assert resolver.resolve_local_document("\u7814\u7a76\u5831") == japanese.resolve()
    assert resolver.resolve_local_document("Cafe final.pdf") is None


@pytest.mark.parametrize("query", ["Course/RA1.pdf", r"Course\RA1.pdf", "Course/RA1"])
def test_shallow_relative_name(document_root, query):
    folder = document_root / "Course"
    folder.mkdir()
    target = folder / "RA1.pdf"
    target.touch()

    assert resolver.resolve_local_document(query) == target.resolve()


def test_prefix_search_is_only_one_directory_deep(document_root):
    shallow = document_root / "Course"
    shallow.mkdir()
    target = shallow / "RA1.pdf"
    target.touch()
    deep = shallow / "Archive"
    deep.mkdir()
    (deep / "RA1 duplicate.pdf").touch()

    assert resolver.resolve_local_document("RA1") == target.resolve()


def test_deeper_document_is_not_discovered(document_root):
    folder = document_root / "Course" / "Archive"
    folder.mkdir(parents=True)
    (folder / "RA1.pdf").touch()

    assert resolver.resolve_local_document("RA1") is None


def test_absolute_path_must_stay_within_roots(document_root, tmp_path):
    target = document_root / "RA1.pdf"
    target.touch()
    outside = tmp_path / "private.pdf"
    outside.touch()

    assert resolver.resolve_local_document(str(target)) == target.resolve()
    with pytest.raises(resolver.DocumentResolutionError, match="^document_invalid_name$"):
        resolver.resolve_local_document(str(outside))


def test_absolute_filename_disambiguates_its_directory(document_root):
    target = document_root / "RA1.pdf"
    target.touch()
    nested = document_root / "Course"
    nested.mkdir()
    (nested / "RA1.pdf").touch()

    assert resolver.resolve_local_document(str(target)) == target.resolve()


def test_absolute_path_cannot_bypass_shallow_depth(document_root):
    folder = document_root / "Course" / "Archive"
    folder.mkdir(parents=True)
    target = folder / "RA1.pdf"
    target.touch()

    with pytest.raises(resolver.DocumentResolutionError, match="^document_invalid_name$"):
        resolver.resolve_local_document(str(target))


@pytest.mark.parametrize("suffix", [
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".py", ".js", ".vbs", ".sh", ".lnk", ".url",
    ".html", ".htm", ".hta", ".svg", ".docm", ".dotm", ".pptm", ".ppam", ".xlsm", ".xlam",
    ".doc", ".xls", ".ppt",
])
def test_disallowed_extensions_are_rejected(document_root, suffix):
    target = document_root / f"RA1{suffix}"
    target.touch()

    with pytest.raises(resolver.DocumentResolutionError, match="^document_invalid_name$"):
        resolver.resolve_local_document(target.name)
    assert resolver.resolve_local_document("RA1") is None


@pytest.mark.parametrize("query", [
    "", " ", "../RA1.pdf", r"..\RA1.pdf", "Course/../RA1.pdf", "Course/./RA1.pdf",
    "Course//RA1.pdf", "Course/Archive/RA1.pdf", "RA1.pdf:stream", "RA1.pdf ", "RA1.pdf.",
    r"C:RA1.pdf", r"\\server\share\RA1.pdf", "//server/share/RA1.pdf", r"\\?\C:\RA1.pdf",
    "https://example.com/RA1.pdf", "file:///RA1.pdf", "RA1.pdf --help", '"RA1.pdf"',
    "RA1.pdf;calc", "RA1.pdf & calc", "RA1.pdf|calc", "RA1\n.pdf", "RA1\x00.pdf", "RA*.pdf",
    "%USERPROFILE%/RA1.pdf", "$HOME/RA1.pdf", "~/RA1.pdf", "RA1?", "x" * 241,
])
def test_invalid_names_are_rejected_before_enumeration(document_root, monkeypatch, query):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid input must not enumerate directories")

    monkeypatch.setattr(resolver.os, "scandir", forbidden)

    with pytest.raises(resolver.DocumentResolutionError, match="^document_invalid_name$"):
        resolver.resolve_local_document(query)


def test_budget_exhaustion_never_selects_partial_match(document_root, monkeypatch):
    (document_root / "RA1.pdf").touch()
    (document_root / "unrelated.txt").touch()
    monkeypatch.setattr(resolver, "MAX_SEARCH_ENTRIES", 1)

    with pytest.raises(resolver.DocumentResolutionError, match="^document_search_limit$"):
        resolver.resolve_local_document("RA1.pdf")


def test_budget_is_shared_across_roots(document_root, tmp_path, monkeypatch):
    other = tmp_path / "Other"
    other.mkdir()
    (document_root / "RA1.pdf").touch()
    (other / "other.txt").touch()
    monkeypatch.setenv("JARVIS_DOCUMENT_ROOTS", os.pathsep.join(map(str, [document_root, other])))
    monkeypatch.setattr(resolver, "MAX_SEARCH_ENTRIES", 1)

    with pytest.raises(resolver.DocumentResolutionError, match="^document_search_limit$"):
        resolver.resolve_local_document("RA1")


def test_unsupported_entries_count_towards_budget(document_root, monkeypatch):
    (document_root / "payload.exe").touch()
    (document_root / "other.exe").touch()
    monkeypatch.setattr(resolver, "MAX_SEARCH_ENTRIES", 1)

    with pytest.raises(resolver.DocumentResolutionError, match="^document_search_limit$"):
        resolver.resolve_local_document("RA1")


def test_duplicate_configured_roots_do_not_create_ambiguity(document_root, monkeypatch):
    target = document_root / "RA1.pdf"
    target.touch()
    monkeypatch.setenv("JARVIS_DOCUMENT_ROOTS", os.pathsep.join([str(document_root)] * 2))

    assert resolver.resolve_local_document("RA1") == target.resolve()


def test_root_count_is_bounded(document_root, monkeypatch):
    monkeypatch.setattr(resolver, "MAX_DOCUMENT_ROOTS", 1)
    monkeypatch.setenv("JARVIS_DOCUMENT_ROOTS", os.pathsep.join([str(document_root), str(document_root.parent)]))

    with pytest.raises(resolver.DocumentResolutionError, match="^document_search_limit$"):
        resolver.resolve_local_document("RA1")


@pytest.mark.parametrize("configured", ["relative", r"\\server\share", "https://example.com/docs"])
def test_invalid_explicit_roots_fail_closed(document_root, monkeypatch, configured):
    monkeypatch.setenv("JARVIS_DOCUMENT_ROOTS", configured)

    with pytest.raises(resolver.DocumentResolutionError, match="^document_invalid_roots$"):
        resolver.resolve_local_document("RA1")


@pytest.mark.parametrize("configured", [None, "", " \t "])
def test_default_roots_are_only_user_desktops(tmp_path, monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv("JARVIS_DOCUMENT_ROOTS", raising=False)
    else:
        monkeypatch.setenv("JARVIS_DOCUMENT_ROOTS", configured)
    monkeypatch.delenv("JARVIS_DESKTOP_HOME", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    target = desktop / "RA1.pdf"
    target.touch()
    (tmp_path / "private.pdf").touch()

    assert resolver.document_search_roots() == (desktop, tmp_path / "Escritorio")
    assert resolver.resolve_local_document("RA1") == target.resolve()
    assert resolver.resolve_local_document("private") is None


@pytest.mark.parametrize("form", ["only", "leading", "trailing", "repeated"])
def test_empty_root_list_items_are_invalid(document_root, monkeypatch, form):
    configured = {
        "only": os.pathsep,
        "leading": os.pathsep + str(document_root),
        "trailing": str(document_root) + os.pathsep,
        "repeated": os.pathsep.join([str(document_root), " ", str(document_root)]),
    }
    monkeypatch.setenv("JARVIS_DOCUMENT_ROOTS", configured[form])

    with pytest.raises(resolver.DocumentResolutionError, match="^document_invalid_roots$"):
        resolver.resolve_local_document("RA1")


def test_blank_document_roots_honor_explicit_desktop_home(document_root, monkeypatch):
    target = document_root / "RA1.pdf"
    target.touch()
    monkeypatch.setenv("JARVIS_DOCUMENT_ROOTS", "")
    monkeypatch.setenv("JARVIS_DESKTOP_HOME", str(document_root))

    assert resolver.document_search_roots() == (document_root,)
    assert resolver.resolve_local_document("RA1") == target.resolve()


def test_explicit_roots_replace_default_desktop(document_root, tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    (desktop / "RA1.pdf").touch()
    monkeypatch.setenv("JARVIS_DESKTOP_HOME", str(desktop))

    assert resolver.resolve_local_document("RA1") is None


def test_unavailable_search_has_controlled_diagnostic(document_root, monkeypatch):
    def unavailable(*args, **kwargs):
        raise PermissionError("private provider details")

    monkeypatch.setattr(resolver.os, "scandir", unavailable)

    with pytest.raises(resolver.DocumentResolutionError) as error:
        resolver.resolve_local_document("RA1")

    assert error.value.diagnostic_code == "document_search_unavailable"
    assert error.value.args == ("document_search_unavailable",)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_symlink_escape_is_not_followed(document_root, tmp_path, kind):
    outside = tmp_path / "Outside"
    outside.mkdir()
    (outside / "RA1.pdf").touch()
    link = document_root / ("RA1.pdf" if kind == "file" else "Course")
    target = outside / "RA1.pdf" if kind == "file" else outside
    try:
        link.symlink_to(target, target_is_directory=kind == "directory")
    except OSError:
        pytest.skip("Creating symlinks requires Windows developer mode or elevation")

    assert resolver.resolve_local_document("RA1") is None


def test_resolved_path_escape_is_rejected(document_root, tmp_path, monkeypatch):
    candidate = document_root / "RA1.pdf"
    candidate.touch()
    outside = tmp_path / "outside.pdf"
    outside.touch()
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        return outside if path == candidate else original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)

    assert resolver.resolve_local_document("RA1") is None


def test_resolved_target_cannot_change_document_type(document_root, monkeypatch):
    candidate = document_root / "RA1.pdf"
    candidate.touch()
    executable = document_root / "payload.exe"
    executable.touch()
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        return executable if path == candidate else original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)

    assert resolver.resolve_local_document("RA1") is None


def test_configured_root_cannot_traverse_symlink_ancestor(document_root, tmp_path, monkeypatch):
    outside = tmp_path / "Outside"
    outside.mkdir()
    nested = outside / "Nested"
    nested.mkdir()
    (nested / "RA1.pdf").touch()
    link = document_root / "Link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires Windows developer mode or elevation")
    monkeypatch.setenv("JARVIS_DOCUMENT_ROOTS", str(link / "Nested"))

    with pytest.raises(resolver.DocumentResolutionError, match="^document_invalid_roots$"):
        resolver.resolve_local_document("RA1")


@pytest.mark.skipif(os.name != "nt", reason="Windows mapped drive validation")
def test_remote_drive_root_is_rejected_before_search(document_root, monkeypatch):
    monkeypatch.setattr(resolver, "_is_local_windows_drive", lambda anchor: False)

    with pytest.raises(resolver.DocumentResolutionError, match="^document_invalid_roots$"):
        resolver.resolve_local_document("RA1")
