#!/usr/bin/env python3
"""Validate the generated Markdown documentation contract."""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Annotated, cast
from urllib.parse import unquote, urlsplit

import typer

app = typer.Typer(
    help=__doc__,
    pretty_exceptions_show_locals=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)

_FENCE_PATTERN = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SETEXT_HEADING_PATTERN = re.compile(r"^\s{0,3}(=+|-+)\s*$")
_REFERENCE_DEFINITION_PATTERN = re.compile(
    r"^[ \t]{0,3}\[([^\]\n]+)\]:[ \t]*(<[^>\n]+>|\S+).*$",
    re.MULTILINE,
)
_REFERENCE_USAGE_PATTERN = re.compile(r"!?\[([^\]\n]*)\]\[([^\]\n]*)\]")
_SHORTCUT_REFERENCE_PATTERN = re.compile(r"!?\[([^\]\n]+)\]")
_ABSOLUTE_LOCAL_PATH_PATTERNS = (
    re.compile(
        r"(?<![\w])/(?:home|tmp|Users|workspace|workspaces|var/tmp|private/tmp)/"
        r"[^\s`'\"\])}]+",
    ),
    re.compile(r"\b[A-Za-z]:\\Users\\[^\s`'\"\])}]+"),
)
_TEMPORARY_MARKERS = (
    "codex prompt",
    "conversation transcript",
    "todo from chat",
)
_ALLOWED_LINK_PREFIXES = ("http://", "https://", "mailto:")
_IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".agents",
        ".aws",
        ".local",
        ".serena",
        "backups",
        ".pytest_cache",
        ".codex",
        ".git",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "build",
        "dist",
        "site",
    }
)
_FENCE_LANGUAGES = frozenset(
    {"bash", "cmake", "cpp", "json", "python", "text", "toml", "yaml"},
)


def _relative_display(path: Path, repository_root: Path) -> str:
    try:
        return path.relative_to(repository_root).as_posix()
    except ValueError:
        return str(path)


def _read_text(path: Path, errors: list[str], repository_root: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exception:
        errors.append(
            f"{_relative_display(path, repository_root)}: "
            f"cannot read UTF-8 Markdown: {exception}",
        )
        return None


def _without_html_comments(text: str) -> str:
    characters = list(text)
    position = 0
    code_ticks: int | None = None
    while position < len(text):
        if text[position] == "`" and not _is_escaped(text, position):
            end = position
            while end < len(text) and text[end] == "`":
                end += 1
            run_length = end - position
            if code_ticks == run_length:
                code_ticks = None
            elif code_ticks is None and _has_closing_backtick_run(
                text, end, run_length
            ):
                code_ticks = run_length
            position = end
            continue
        if code_ticks is None and text.startswith("<!--", position):
            closing = text.find("-->", position + 4)
            end = len(text) if closing == -1 else closing + 3
            for index in range(position, end):
                if characters[index] != "\n":
                    characters[index] = " "
            position = end
            continue
        position += 1
    return "".join(characters)


def _has_closing_backtick_run(text: str, position: int, run_length: int) -> bool:
    while position < len(text):
        if text[position] != "`" or _is_escaped(text, position):
            position += 1
            continue
        end = position
        while end < len(text) and text[end] == "`":
            end += 1
        if end - position == run_length:
            return True
        position = end
    return False


def _prose_lines(
    path: Path,
    text: str,
    errors: list[str],
    repository_root: Path,
) -> list[str]:
    lines = text.splitlines()
    prose = list(lines)
    active_fence: tuple[str, int] | None = None

    for line_number, line in enumerate(lines, start=1):
        match = _FENCE_PATTERN.match(line)
        if active_fence is None:
            if match is None:
                continue
            marker = match.group(1)
            language = match.group(2).strip()
            if not language:
                errors.append(
                    f"{_relative_display(path, repository_root)}:{line_number}: "
                    "fenced code block has no language",
                )
            elif language not in _FENCE_LANGUAGES:
                errors.append(
                    f"{_relative_display(path, repository_root)}:{line_number}: "
                    f"unsupported fenced code language {language!r}",
                )
            active_fence = (marker[0], len(marker))
            prose[line_number - 1] = ""
            continue

        prose[line_number - 1] = ""
        if match is None:
            continue
        marker = match.group(1)
        if (
            marker[0] == active_fence[0]
            and len(marker) >= active_fence[1]
            and not match.group(2).strip()
        ):
            active_fence = None

    if active_fence is not None:
        errors.append(
            f"{_relative_display(path, repository_root)}: "
            "fenced code block is not closed",
        )
    return _without_html_comments("\n".join(prose)).split("\n")


def _is_opening_paragraph(line: str) -> bool:
    if line.startswith(("    ", "\t")) or _REFERENCE_DEFINITION_PATTERN.match(line):
        return False
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith(("#", ">", "|", "<", "```", "~~~")):
        return False
    if re.match(r"(?:[-+*]|\d+[.)])\s+", stripped):
        return False
    return stripped not in {"---", "***", "___"}


def _check_page_structure(
    path: Path,
    text: str,
    prose: list[str],
    errors: list[str],
    repository_root: Path,
) -> None:
    headings: list[tuple[int, str, str]] = []
    for line_number, line in enumerate(prose, start=1):
        if match := _HEADING_PATTERN.match(line):
            headings.append((line_number, match.group(1), match.group(2)))
            continue
        if (
            (match := _SETEXT_HEADING_PATTERN.match(line))
            and line_number > 1
            and prose[line_number - 2].strip()
        ):
            marker = "#" if match.group(1).startswith("=") else "##"
            headings.append((line_number, marker, prose[line_number - 2].strip()))
    h1_headings = [heading for heading in headings if heading[1] == "#"]
    display = _relative_display(path, repository_root)

    if len(h1_headings) != 1:
        errors.append(f"{display}: expected exactly one H1, found {len(h1_headings)}")
        return

    h1_line = h1_headings[0][0]
    opening_line = next(
        (line for line in prose[h1_line:] if line.strip()),
        None,
    )
    if opening_line is None or not _is_opening_paragraph(opening_line):
        errors.append(
            f"{display}: H1 must be followed by a non-empty opening paragraph"
        )

    lowered = text.casefold()
    for marker in _TEMPORARY_MARKERS:
        if marker in lowered:
            errors.append(
                f"{display}: contains temporary prompt/chat marker {marker!r}"
            )

    for pattern in _ABSOLUTE_LOCAL_PATH_PATTERNS:
        for match in pattern.finditer(text):
            line_number = text.count("\n", 0, match.start()) + 1
            errors.append(
                f"{display}:{line_number}: contains absolute local filesystem path "
                f"{match.group(0)!r}",
            )


def _reference_label(value: str) -> str:
    return " ".join(value.split()).casefold()


def _inside_any_span(position: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in spans)


def _markdown_link_text(prose: list[str]) -> str:
    text = _without_html_comments("\n".join(prose))
    return re.sub(r"(`+).*?\1", "", text, flags=re.DOTALL)


def _is_escaped(text: str, position: int) -> bool:
    backslashes = 0
    position -= 1
    while position >= 0 and text[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 1


def _closing_square_bracket(text: str, opening: int) -> int | None:
    depth = 0
    for position in range(opening, len(text)):
        character = text[position]
        if _is_escaped(text, position):
            continue
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth == 0:
                return position
    return None


def _parenthesized_destination(text: str, opening: int) -> tuple[str, int] | None:
    position = opening + 1
    while position < len(text) and text[position].isspace():
        position += 1
    destination_start = position
    angle_destination = position < len(text) and text[position] == "<"
    angle_end: int | None = None
    depth = 1
    destination_end: int | None = None

    while position < len(text):
        character = text[position]
        if _is_escaped(text, position):
            position += 1
            continue
        if angle_destination and angle_end is None:
            if character == ">":
                angle_end = position + 1
            position += 1
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                if angle_destination:
                    if angle_end is None:
                        return None
                    destination_end = angle_end
                elif destination_end is None:
                    destination_end = position
                return text[destination_start:destination_end], position + 1
        elif character.isspace() and depth == 1 and destination_end is None:
            destination_end = position
        position += 1
    return None


def _inline_link_destinations(text: str) -> list[tuple[str, bool, tuple[int, int]]]:
    destinations: list[tuple[str, bool, tuple[int, int]]] = []
    for position, character in enumerate(text):
        is_image = (
            character == "!" and position + 1 < len(text) and text[position + 1] == "["
        )
        if is_image:
            bracket = position + 1
        elif character == "[":
            if (
                position > 0
                and text[position - 1] == "!"
                and not _is_escaped(text, position - 1)
            ):
                continue
            bracket = position
        else:
            continue
        if _is_escaped(text, position):
            continue

        closing_bracket = _closing_square_bracket(text, bracket)
        if closing_bracket is None or closing_bracket + 1 >= len(text):
            continue
        opening_parenthesis = closing_bracket + 1
        if text[opening_parenthesis] != "(":
            continue
        parsed = _parenthesized_destination(text, opening_parenthesis)
        if parsed is None:
            continue
        destination, end = parsed
        destinations.append((destination, not is_image, (position, end)))
    return destinations


def _link_destinations(prose: list[str]) -> list[tuple[str, bool]]:
    text = _markdown_link_text(prose)
    definitions: dict[str, str] = {}
    occupied_spans: list[tuple[int, int]] = []
    for match in _REFERENCE_DEFINITION_PATTERN.finditer(text):
        definitions[_reference_label(match.group(1))] = match.group(2)
        occupied_spans.append(match.span())

    destinations: list[tuple[str, bool]] = []
    for destination, navigable, span in _inline_link_destinations(text):
        destinations.append((destination, navigable))
        occupied_spans.append(span)

    used_references: dict[str, bool] = {}
    for match in _REFERENCE_USAGE_PATTERN.finditer(text):
        if _inside_any_span(match.start(), occupied_spans):
            continue
        label = match.group(2) or match.group(1)
        normalized_label = _reference_label(label)
        used_references[normalized_label] = used_references.get(
            normalized_label,
            False,
        ) or not match.group(0).startswith("!")
        occupied_spans.append(match.span())

    for match in _SHORTCUT_REFERENCE_PATTERN.finditer(text):
        if _inside_any_span(match.start(), occupied_spans):
            continue
        label = _reference_label(match.group(1))
        if label in definitions:
            used_references[label] = used_references.get(
                label, False
            ) or not match.group(
                0,
            ).startswith("!")

    destinations.extend(
        (destination, used_references[label])
        for label, destination in definitions.items()
        if label in used_references
    )
    return [
        (item[1:-1] if item.startswith("<") and item.endswith(">") else item, navigable)
        for item, navigable in destinations
    ]


def _plain_heading_text(value: str) -> str:
    value = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"<[^>]+>", "", value)
    return value.replace("`", "").replace("*", "").strip(" #")


def _github_slug(value: str) -> str:
    plain = _plain_heading_text(value).casefold()
    characters = [
        character for character in plain if character.isalnum() or character in " _-"
    ]
    return re.sub(r"\s", "-", "".join(characters)).strip("-")


def _heading_anchors(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    resolved = path.resolve()
    if resolved in cache:
        return cache[resolved]

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        cache[resolved] = set()
        return cache[resolved]

    lines = _prose_lines(path, text, [], path.parent)

    anchors: set[str] = set()
    counts: defaultdict[str, int] = defaultdict(int)
    previous_prose_line = ""
    for line in lines:
        heading_match = _HEADING_PATTERN.match(line)
        if heading_match is not None:
            heading_text = heading_match.group(2)
        elif _SETEXT_HEADING_PATTERN.match(line) and previous_prose_line.strip():
            heading_text = previous_prose_line.strip()
        else:
            previous_prose_line = line
            continue
        base = _github_slug(heading_text)
        occurrence = counts[base]
        counts[base] += 1
        anchors.add(base if occurrence == 0 else f"{base}-{occurrence}")
        previous_prose_line = ""

    cache[resolved] = anchors
    return anchors


def _check_links(
    pages: dict[Path, list[str]],
    repository_root: Path,
    errors: list[str],
) -> None:
    anchor_cache: dict[Path, set[str]] = {}

    for source, prose in pages.items():
        display = _relative_display(source, repository_root)
        for destination, _navigable in _link_destinations(prose):
            if not destination.strip():
                errors.append(f"{display}: local link destination is empty")
                continue
            if destination.startswith("#"):
                fragment = unquote(destination[1:])
                anchors = _heading_anchors(source, anchor_cache)
                if not fragment or fragment.casefold() not in {
                    anchor.casefold() for anchor in anchors
                }:
                    errors.append(
                        f"{display}: same-document anchor does not exist: {destination}"
                    )
                continue

            if destination.casefold().startswith(_ALLOWED_LINK_PREFIXES):
                continue

            try:
                parsed = urlsplit(destination)
            except ValueError:
                errors.append(f"{display}: malformed local link: {destination}")
                continue
            if (
                parsed.scheme
                or parsed.netloc
                or parsed.query
                or parsed.path.startswith("/")
            ):
                errors.append(f"{display}: unsupported local link: {destination}")
                continue
            target = (
                (source.parent / unquote(parsed.path)).resolve()
                if parsed.path
                else source.resolve()
            )
            if source.suffix == ".jinja" and not target.is_file():
                target = target.with_name(target.name + ".jinja")
            if not target.is_relative_to(repository_root) or not target.is_file():
                errors.append(f"{display}: local link target is missing: {destination}")
                continue
            if parsed.fragment:
                fragment = unquote(parsed.fragment).casefold()
                if ".md" not in target.suffixes or fragment not in {
                    anchor.casefold()
                    for anchor in _heading_anchors(target, anchor_cache)
                }:
                    errors.append(
                        f"{display}: local link anchor does not exist: {destination}"
                    )


def _check_index_inventory(
    index: Path,
    docs_directory: Path,
    pages: dict[Path, list[str]],
    repository_root: Path,
    errors: list[str],
) -> None:
    index_text = index.read_text(encoding="utf-8")
    for page in sorted(pages):
        if page == index.resolve() or not page.is_relative_to(docs_directory.resolve()):
            continue
        reference = f"`{_relative_display(page, repository_root)}`"
        if reference not in index_text:
            errors.append(
                f"{_relative_display(page, repository_root)}: "
                "page is not listed by its "
                f"repository-root path in {_relative_display(index, repository_root)}"
            )


def _is_ignored_document(path: Path, repository_root: Path) -> bool:
    relative_parts = path.relative_to(repository_root).parts
    return any(part in _IGNORED_DIRECTORY_NAMES for part in relative_parts)


def check_documents(
    documents: list[Path] | None, docs_dir: Path, selected_index: Path | None
) -> int:
    """Validate paths independently of command-line parsing."""
    repository_root = Path.cwd().resolve()
    errors: list[str] = []

    if documents:
        document_paths = [
            path.resolve() if path.is_absolute() else (repository_root / path).resolve()
            for path in documents
        ]
        index = None
        docs_directory = None
    else:
        docs_directory = (
            docs_dir.resolve()
            if docs_dir.is_absolute()
            else (repository_root / docs_dir).resolve()
        )
        index_argument = selected_index or docs_dir / "index.md"
        index = (
            index_argument.resolve()
            if index_argument.is_absolute()
            else (repository_root / index_argument).resolve()
        )
        if not index.is_file():
            errors.append(
                f"{_relative_display(index, repository_root)}: "
                "documentation index is missing",
            )
        document_paths = sorted(
            path
            for path in repository_root.rglob("*.md")
            if not _is_ignored_document(path, repository_root)
        )
        if not document_paths:
            errors.append(
                f"{_relative_display(docs_directory, repository_root)}: "
                "no Markdown pages found",
            )

    pages: dict[Path, list[str]] = {}
    for path in document_paths:
        if not path.is_file():
            errors.append(
                f"{_relative_display(path, repository_root)}: "
                "Markdown document is missing",
            )
            continue
        text = _read_text(path, errors, repository_root)
        if text is None:
            continue
        prose = _prose_lines(path, text, errors, repository_root)
        pages[path.resolve()] = prose
        _check_page_structure(path, text, prose, errors, repository_root)

    _check_links(pages, repository_root, errors)
    if index is not None and docs_directory is not None and index.resolve() in pages:
        _check_index_inventory(index, docs_directory, pages, repository_root, errors)

    if errors:
        print("Documentation check failed:", file=sys.stderr)
        for error in sorted(set(errors)):
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"Documentation contract passed for {len(pages)} page(s).")
    return 0


@app.command(name="check")
def check(
    documents: Annotated[
        Path | None, typer.Option("--documents", help="Explicit Markdown files.")
    ] = None,
    remaining_documents: Annotated[
        list[Path] | None, typer.Argument(help="More files after --documents.")
    ] = None,
    docs_dir: Annotated[Path, typer.Option("--docs-dir")] = Path("docs"),
    index: Annotated[Path | None, typer.Option("--index")] = None,
) -> None:
    if remaining_documents and documents is None:
        raise typer.BadParameter("Extra document paths require --documents.")
    paths = [documents, *(remaining_documents or [])] if documents else None
    raise typer.Exit(check_documents(paths, docs_dir, index))


def main() -> int:
    """Retain the callable exit-status interface for existing consumers."""
    return cast(int, app(standalone_mode=False))


if __name__ == "__main__":
    app()
