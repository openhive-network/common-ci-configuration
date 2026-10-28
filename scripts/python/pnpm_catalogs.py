'''
Reads the `catalogs:` mapping of a pnpm-workspace.yaml without a YAML library,
so the scripts using it run with any python3 (the emsdk and node-test-tools
images ship no PyYAML).

Only the block style pnpm-workspace.yaml files use is accepted: `catalogs:` at
column 0, catalog names one level in, `package: version` pairs below them.
Anything else in the block is a CatalogError naming the line.
'''
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_WORKSPACE = Path(__file__).resolve().parent.parent.parent / "pnpm-config" / "pnpm-workspace.yaml"

Catalogs = Dict[str, Dict[str, str]]

_ENTRY = re.compile(
    r"^(?P<indent> *)(?P<key>'(?:[^']|'')*'|\"(?:[^\"\\]|\\.)*\"|[^\s#'\"][^:#]*?)"
    r"[ \t]*:(?:[ \t]+(?P<value>[^#]*?))?[ \t]*(?:#.*)?$"
)
_PLAIN = re.compile(r"[A-Za-z][\w.-]*")
_YAML_WORDS = {"true", "false", "yes", "no", "on", "off", "null", "y", "n"}


class CatalogError(Exception):
    """The file's `catalogs:` block is not the block style this module reads."""


def is_content(line: str) -> bool:
    """`line` is neither blank nor a comment."""
    return bool(line.strip()) and not line.lstrip().startswith("#")


def unquote(scalar: str) -> str:
    """Value of a plain, 'single' or "double" quoted YAML scalar."""
    if len(scalar) >= 2 and scalar[0] == scalar[-1] == "'":
        return scalar[1:-1].replace("''", "'")
    if len(scalar) >= 2 and scalar[0] == scalar[-1] == '"':
        return re.sub(r"\\(.)", r"\1", scalar[1:-1])
    return scalar


def quote(scalar: str) -> str:
    """`scalar` as YAML: plain when that reads back as the same string, else single quoted."""
    if _PLAIN.fullmatch(scalar) and scalar.lower() not in _YAML_WORDS:
        return scalar
    return "'" + scalar.replace("'", "''") + "'"


def parse_entry(line: str, number: int) -> Tuple[int, str, Optional[str]]:
    """(indent, key, value or None) of mapping line `line` (1-based `number` for errors)."""
    match = _ENTRY.match(line.rstrip("\r\n"))
    if not match:
        raise CatalogError(f"line {number}: not a `key: value` line: {line.strip()}")
    value = match.group("value")
    return len(match.group("indent")), unquote(match.group("key")), unquote(value) if value else None


def catalogs_span(lines: List[str]) -> Optional[Tuple[int, int]]:
    """(index of the `catalogs:` line, index past its block) in `lines`, None without one.

    The block ends at the next column-0 key; indented comments are part of it,
    column-0 comments and blank lines before that key are not.
    """
    starts = [i for i, line in enumerate(lines) if re.match(r"catalogs[ \t]*:", line)]
    if not starts:
        return None
    if len(starts) > 1:
        raise CatalogError(f"lines {', '.join(str(i + 1) for i in starts)}: more than one `catalogs:` key")
    start = starts[0]
    if parse_entry(lines[start], start + 1)[2] is not None:
        raise CatalogError(f"line {start + 1}: `catalogs:` must be a block mapping, not an inline value")
    end = start + 1
    for i in range(start + 1, len(lines)):
        if is_content(lines[i]) and not lines[i][0].isspace():
            break
        if lines[i].strip() and lines[i][0].isspace():
            end = i + 1
    return start, end


def parse_block(lines: List[str], first: int, end: int) -> Catalogs:
    """Catalogs defined by `lines[first:end]`, the content of a `catalogs:` block."""
    catalogs: Catalogs = {}
    catalog_indent = None
    package_indent = None
    current: Optional[Dict[str, str]] = None
    for i in range(first, end):
        if not is_content(lines[i]):
            continue
        indent, key, value = parse_entry(lines[i], i + 1)
        if catalog_indent is None:
            catalog_indent = indent
        if indent == catalog_indent:
            if value is not None:
                raise CatalogError(f"line {i + 1}: catalog {key!r} must be a block mapping")
            if key in catalogs:
                raise CatalogError(f"line {i + 1}: catalog {key!r} is defined twice")
            current = catalogs[key] = {}
            package_indent = None
            continue
        if current is None or indent < catalog_indent:
            raise CatalogError(f"line {i + 1}: unexpected indentation")
        if package_indent is None:
            package_indent = indent
        if indent != package_indent or value is None:
            raise CatalogError(f"line {i + 1}: expected `package: version` at the catalog's indentation")
        if key in current:
            raise CatalogError(f"line {i + 1}: package {key!r} is listed twice in its catalog")
        current[key] = value
    return catalogs


def parse_catalogs(text: str) -> Catalogs:
    """Catalogs of pnpm-workspace.yaml content `text`, in file order ({} without a block)."""
    lines = text.splitlines(keepends=True)
    span = catalogs_span(lines)
    return parse_block(lines, span[0] + 1, span[1]) if span else {}
