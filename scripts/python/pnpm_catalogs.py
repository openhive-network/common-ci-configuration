'''
Reads the `catalogs:` mapping of a pnpm-workspace.yaml with the standard
library only: the images that run the catalog scripts (emsdk, node-test-tools)
ship python3 without PyYAML.

Only the block style pnpm-workspace.yaml files use is read: `catalogs:` at
column 0, one block mapping per catalog, one `package: version` per line, plain
or quoted scalars, comments. Anything else in the block is a CatalogError
naming the line.
'''
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CCC_WORKSPACE = Path(__file__).resolve().parents[2] / "pnpm-config" / "pnpm-workspace.yaml"

Catalogs = Dict[str, Dict[str, str]]

_SCALAR = r"\"(?:[^\"\\]|\\.)*\"|'(?:[^']|'')*'"
_ENTRY = re.compile(
    rf"(?P<indent> *)(?P<key>{_SCALAR}|[^\s\"'#][^:#]*?)[ \t]*:"
    rf"(?:[ \t]+(?P<value>{_SCALAR}|[^\s\"'#][^#]*?))?[ \t]*(?:#.*)?"
)


class CatalogError(ValueError):
    """The `catalogs:` block is not in the block style this module reads."""


def is_content(line: str) -> bool:
    """True when `line` is neither blank nor only a comment."""
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def indent_of(line: str) -> int:
    """Number of leading spaces of `line`."""
    return len(line) - len(line.lstrip(" "))


def _unquote(scalar: str) -> str:
    if scalar.startswith('"'):
        value = json.loads(scalar)
        if not isinstance(value, str):
            raise CatalogError(f"not a string scalar: {scalar}")
        return value
    if scalar.startswith("'"):
        return scalar[1:-1].replace("''", "'")
    return scalar


def to_yaml(scalar: str) -> str:
    """`scalar` as a double-quoted YAML scalar (a JSON string is one)."""
    return json.dumps(scalar)


def _entry(line: str, number: int) -> Tuple[int, str, Optional[str]]:
    match = _ENTRY.fullmatch(line.rstrip("\r\n"))
    if not match:
        raise CatalogError(f"line {number}: expected `key: value`, got {line.strip()!r}")
    value = match.group("value")
    return len(match.group("indent")), _unquote(match.group("key")), None if value is None else _unquote(value)


def find_block(lines: List[str]) -> Optional[Tuple[int, int]]:
    """(index of the `catalogs:` line, index past its block's last indented line), None without one.

    Raises CatalogError for a second `catalogs:` key or an inline value.
    """
    starts = [i for i, line in enumerate(lines) if re.match(r"catalogs[ \t]*:", line)]
    if not starts:
        return None
    if len(starts) > 1:
        raise CatalogError(f"lines {', '.join(str(i + 1) for i in starts)}: `catalogs:` is defined more than once")
    start = starts[0]
    if _entry(lines[start], start + 1)[2] is not None:
        raise CatalogError(f"line {start + 1}: `catalogs:` must be a block mapping")
    end = start + 1
    for i in range(start + 1, len(lines)):
        if is_content(lines[i]) and indent_of(lines[i]) == 0:
            break
        if lines[i].strip():
            end = i + 1
    return start, end


def parse_block(numbered_lines: List[Tuple[int, str]]) -> Catalogs:
    """Catalogs of the (1-based line number, line) content of a `catalogs:` block.

    Raises CatalogError naming the line for anything but catalogs of `package: version`
    lines, or for a catalog or package defined twice.
    """
    catalogs: Catalogs = {}
    catalog_indent: Optional[int] = None
    package_indent: Optional[int] = None
    current: Optional[Dict[str, str]] = None
    for number, line in numbered_lines:
        if not is_content(line):
            continue
        indent, key, value = _entry(line, number)
        if catalog_indent is None:
            catalog_indent = indent
        if indent == catalog_indent:
            if value is not None:
                raise CatalogError(f"line {number}: catalog {key!r} must be a block mapping of packages")
            if key in catalogs:
                raise CatalogError(f"line {number}: catalog {key!r} is defined twice")
            current = catalogs[key] = {}
            package_indent = None
            continue
        if current is None or indent < catalog_indent:
            raise CatalogError(f"line {number}: unexpected indentation")
        if package_indent is None:
            package_indent = indent
        if indent != package_indent or value is None:
            raise CatalogError(f"line {number}: expected `package: version` at the catalog's indentation")
        if key in current:
            raise CatalogError(f"line {number}: package {key!r} is listed twice in its catalog")
        current[key] = value
    return catalogs


def read_catalogs(text: str) -> Catalogs:
    """Catalogs of pnpm-workspace.yaml content `text` in file order ({} without `catalogs:`)."""
    lines = text.splitlines(keepends=True)
    block = find_block(lines)
    if block is None:
        return {}
    start, end = block
    return parse_block([(i + 1, lines[i]) for i in range(start + 1, end)])
