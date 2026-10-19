"""Check Markdown parsing, diagnostics, links and CLI against real temporary files."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tools import check_documentation as checker


def document(
    root: Path, name: str, text: str = "# Guide\n\nOpening paragraph.\n"
) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def check(root: Path, text: str) -> list[str]:
    path = document(root, "README.md", text)
    errors: list[str] = []
    prose = checker._prose_lines(path, text, errors, root)
    checker._check_page_structure(path, text, prose, errors, root)
    return errors


@pytest.mark.parametrize(
    "text",
    [
        "# Guide\n\nOpening.\n",
        "Guide\n=====\n\nOpening.\n",
        "# Guide\n\nOpening.\n\n## Details\n",
        "# Guide\n\nOpening.\n```python\n# hidden heading\n```\n",
        "# Guide\n\nOpening.\n~~~text\n[x](missing)\n~~~~\n",
        "<!-- # Hidden -->\n# Guide\n\nOpening.\n",
    ],
)
def test_valid_structure_ignores_code_and_comments(tmp_path: Path, text: str) -> None:
    assert check(tmp_path, text) == []


def test_fence_like_content_does_not_close_a_different_or_longer_fence(
    tmp_path: Path,
) -> None:
    assert (
        check(
            tmp_path,
            "# Guide\n\nOpening.\n````text\n~~~\n```\n````python\n````\n",
        )
        == []
    )
    assert not checker._is_opening_paragraph("")
    assert not checker._is_opening_paragraph("[reference]: guide.md")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("no heading", "exactly one H1"),
        ("# One\n\nOpening.\n# Two\n", "exactly one H1"),
        ("# Empty\n", "opening paragraph"),
        ("# Guide\n\n- list\n", "opening paragraph"),
        ("# Guide\n\n    indented\n", "opening paragraph"),
        ("# Guide\n\n> quote\n", "opening paragraph"),
        ("# Guide\n\n---\n", "opening paragraph"),
        ("# Guide\n\nOpening.\n```\nx\n```\n", "no language"),
        ("# Guide\n\nOpening.\n```unknown\nx\n```\n", "unsupported"),
        ("# Guide\n\nOpening.\n```text\nx\n", "not closed"),
        ("# Guide\n\nOpening.\nCodex prompt\n", "temporary"),
        ("# Guide\n\nOpening.\n`/home/synthetic/file`\n", "absolute local"),
        ("# Guide\n\nOpening.\n`C:\\Users\\synthetic\\file`\n", "absolute local"),
    ],
)
def test_structure_reports_real_invalid_markdown(
    tmp_path: Path, text: str, message: str
) -> None:
    assert any(message in error for error in check(tmp_path, text))


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("[nested [label]](a(b).md)", [("a(b).md", True)]),
        (
            "![image](<asset with spaces.png> 'Title')",
            [("asset with spaces.png", False)],
        ),
        (r"\[escaped](missing)", []),
        (r"\![visible](guide.md)", [("guide.md", True)]),
        (r"[escaped\] label](guide.md)", [("guide.md", True)]),
        ("[unclosed", []),
        ("[label] no destination", []),
        ("[label](unclosed", []),
        ("[label](<unclosed)", []),
        ("`[code](missing)` [real](guide.md)", [("guide.md", True)]),
        ("[label]( guide.md 'Title')", [("guide.md", True)]),
        (r"[label](a\(b\).md)", [(r"a\(b\).md", True)]),
    ],
)
def test_inline_destinations_preserve_markdown_semantics(
    line: str, expected: list[tuple[str, bool]]
) -> None:
    assert checker._link_destinations([line]) == expected


def test_full_collapsed_shortcut_and_image_references_share_definitions() -> None:
    text = [
        "[label][ Guide ] [guide][] [guide] ![image][asset] [image-link][asset]",
        "[guide]: <guide.md>",
        "[asset]: asset.png",
        "[unused]: missing.md",
    ]
    assert checker._link_destinations(text) == [("guide.md", True), ("asset.png", True)]
    assert checker._link_destinations(["![guide]", "[guide]: guide.md"]) == [
        ("guide.md", False)
    ]
    assert checker._link_destinations(["[nested [x][y]](guide.md)"]) == [
        ("guide.md", True)
    ]


def test_comments_hide_links_but_inline_code_preserves_comment_literals() -> None:
    text = "<!-- [hidden](missing) --> `[literal](missing)` [real](guide.md)"
    assert checker._link_destinations([text]) == [("guide.md", True)]
    assert "<!-- literal -->" in checker._without_html_comments("`<!-- literal -->`")
    assert (
        checker._without_html_comments("a <!-- unterminated\nb")
        == "a                  \n "
    )
    assert checker._without_html_comments("`unclosed <!-- removed -->").startswith(
        "`unclosed "
    )
    assert checker._has_closing_backtick_run("a `` b `", 0, 1)
    assert not checker._has_closing_backtick_run(r"a \` b", 0, 1)


def test_heading_anchors_include_duplicates_setext_unicode_and_cache(
    tmp_path: Path,
) -> None:
    path = document(
        tmp_path,
        "guide.md",
        "# **Пример** и `код`\nOpening.\n## Repeat\n## Repeat\n"
        "Setext\n------\n![Picture](asset) and [Link](guide) <b>tag</b>\n------\n"
        "```text\n## Hidden\n```\n",
    )
    cache: dict[Path, set[str]] = {}
    expected = {"пример-и-код", "repeat", "repeat-1", "setext", "picture-and-link-tag"}
    assert checker._heading_anchors(path, cache) == expected
    path.unlink()
    assert checker._heading_anchors(path, cache) == expected
    assert checker._heading_anchors(path, {}) == set()
    path.write_bytes(b"\xff")
    assert checker._heading_anchors(path, {}) == set()


@pytest.mark.parametrize(
    "destination",
    [
        "#guide",
        "#GUIDE",
        "guide.md#part",
        "space%20name.md#part",
        "asset.png",
        "https://example.invalid/guide",
        "mailto:user@example.invalid",
    ],
)
def test_local_targets_and_anchors_resolve_without_network(
    tmp_path: Path, destination: str
) -> None:
    source = document(tmp_path, "README.md")
    document(tmp_path, "guide.md", "# Part\n\nOpening.\n")
    document(tmp_path, "space name.md", "# Part\n\nOpening.\n")
    document(tmp_path, "asset.png", "synthetic bitmap placeholder")
    errors: list[str] = []
    checker._check_links({source: [f"[go]({destination})"]}, tmp_path, errors)
    assert errors == []


@pytest.mark.parametrize(
    "destination",
    [
        "",
        "#",
        "#missing",
        "guide.md#missing",
        "asset.png#part",
        "missing.md",
        "../outside.md",
        "/absolute.md",
        "file:///synthetic",
        "//example.invalid/x",
        "guide.md?query=1",
        "//[broken",
    ],
)
def test_unsupported_missing_and_escaping_targets_fail(
    tmp_path: Path, destination: str
) -> None:
    source = document(tmp_path, "README.md")
    document(tmp_path, "guide.md")
    document(tmp_path, "asset.png", "synthetic")
    errors: list[str] = []
    checker._check_links({source: [f"[go]({destination})"]}, tmp_path, errors)
    assert errors


def test_symlink_escape_and_template_suffix_are_resolved(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    source = document(root, "README.md.jinja")
    document(root, "guide.md.jinja", "# Part\n\nOpening.\n")
    outside = document(tmp_path, "outside.md")
    (root / "escape.md").symlink_to(outside)
    errors: list[str] = []
    checker._check_links({source: ["[ok](guide.md#part)"]}, root, errors)
    assert errors == []
    checker._check_links({source: ["[escape](escape.md)"]}, root, errors)
    assert any("missing" in error for error in errors)


def test_utf8_errors_and_outside_display_are_controlled(tmp_path: Path) -> None:
    errors: list[str] = []
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"\xff")
    assert checker._read_text(bad, errors, tmp_path) is None
    assert checker._read_text(tmp_path, errors, tmp_path) is None
    assert len(errors) == 2 and all("cannot read UTF-8" in error for error in errors)
    assert checker._relative_display(tmp_path / "outside", tmp_path / "inside") == str(
        tmp_path / "outside"
    )


def test_document_inventory_ignores_private_metadata(tmp_path: Path) -> None:
    for name in checker._IGNORED_DIRECTORY_NAMES:
        assert checker._is_ignored_document(tmp_path / name / "secret.md", tmp_path)
    assert not checker._is_ignored_document(tmp_path / "docs/guide.md", tmp_path)


def test_main_checks_index_inventory_and_explicit_document_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    guide = document(tmp_path, "docs/guide.md")
    index = document(tmp_path, "docs/index.md", "# Index\n\nRead `docs/guide.md`.\n")
    monkeypatch.setattr(sys, "argv", ["check_documentation"])
    assert checker.main() == 0
    assert "2 page(s)" in capsys.readouterr().out
    index.write_text("# Index\n\nOpening.\n")
    assert checker.main() == 1
    assert "not listed" in capsys.readouterr().err
    monkeypatch.setattr(sys, "argv", ["check_documentation", "--documents", str(guide)])
    assert checker.main() == 0
    monkeypatch.setattr(
        sys, "argv", ["check_documentation", "--documents", "missing.md"]
    )
    assert checker.main() == 1
    assert "document is missing" in capsys.readouterr().err
    guide.write_bytes(b"\xff")
    monkeypatch.setattr(sys, "argv", ["check_documentation", "--documents", str(guide)])
    assert checker.main() == 1
    assert "UTF-8" in capsys.readouterr().err


def test_main_reports_missing_index_and_empty_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["check_documentation"])
    assert checker.main() == 1
    error = capsys.readouterr().err
    assert "index is missing" in error and "no Markdown pages" in error
    index = document(tmp_path, "guide/index.md")
    monkeypatch.setattr(
        sys,
        "argv",
        ["check_documentation", "--docs-dir", str(index.parent), "--index", str(index)],
    )
    assert checker.main() == 0


def test_actual_cli_uses_explicit_documents_from_another_directory(
    tmp_path: Path,
) -> None:
    path = document(tmp_path, "README.md")
    result = subprocess.run(
        [sys.executable, str(Path(checker.__file__)), "--documents", str(path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "1 page(s)" in result.stdout


def test_default_inventory_separates_reports_forms_and_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Machine reports are not guides; GitHub guidance must still be checked."""
    document(tmp_path, "docs/index.md")
    form = document(
        tmp_path, ".github/PULL_REQUEST_TEMPLATE.md", "## Summary\n\nForm.\n"
    )
    report = document(tmp_path, "coverage/summary.md", "| Metric | Coverage |\n")
    document(tmp_path, ".vscode/cache.md", "Editor cache without a heading.\n")
    guidance = document(tmp_path, ".github/docs/sdk-admission.md")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(checker.app, [])
    assert result.exit_code == 0, result.stderr
    assert "2 page(s)" in result.stdout
    guidance.write_text("Guidance requires its own heading.\n")
    result = runner.invoke(checker.app, [])
    assert result.exit_code == 1
    assert ".github/docs/sdk-admission.md" in result.stderr
    assert "coverage/summary.md" not in result.stderr
    for explicit_document in (report, form):
        result = runner.invoke(checker.app, ["--documents", str(explicit_document)])
        assert result.exit_code == 1
        assert "expected exactly one H1" in result.stderr
