"""Read values from all user gitconfig files."""

from __future__ import annotations

import re
from pathlib import Path

_SECTION_RE = re.compile(r"^\[(.+)\]\s*$")
_KEY_VALUE_RE = re.compile(r"^([A-Za-z0-9_-]+)\s*=\s*(.*)$")


def _gitconfig_files() -> list[Path]:
    home = Path.home()
    files: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        resolved = path.expanduser().resolve()
        if not resolved.is_file() or resolved in seen:
            return
        seen.add(resolved)
        files.append(resolved)

    add(home / ".gitconfig")
    for path in sorted(home.glob(".gitconfig*")):
        add(path)

    idx = 0
    while idx < len(files):
        for include_path in _include_paths(files[idx]):
            add(include_path)
        idx += 1

    return files


def _include_paths(path: Path) -> list[Path]:
    paths: list[Path] = []
    in_include = False
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        section = _SECTION_RE.match(stripped)
        if section:
            header = section.group(1)
            in_include = header == "include" or header.startswith("includeIf")
            continue
        if not in_include:
            continue
        match = _KEY_VALUE_RE.match(stripped)
        if match and match.group(1) == "path":
            value = match.group(2).strip().strip('"')
            paths.append(Path(value))
    return paths


def _values_from_file(path: Path, section: str, key: str) -> list[str]:
    values: list[str] = []
    current_section: str | None = None
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        section_match = _SECTION_RE.match(stripped)
        if section_match:
            current_section = section_match.group(1).split()[0]
            continue
        if current_section != section:
            continue
        match = _KEY_VALUE_RE.match(stripped)
        if match and match.group(1) == key:
            value = match.group(2).strip().strip('"')
            if value:
                values.append(value)
    return values


def all_gitconfig_values(dotted_key: str) -> list[str]:
    """Collect unique values for a key like user.name from every gitconfig file."""
    section, key = dotted_key.split(".", 1)
    values: list[str] = []
    seen: set[str] = set()
    for path in _gitconfig_files():
        for value in _values_from_file(path, section, key):
            lowered = value.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            values.append(value)
    return values
