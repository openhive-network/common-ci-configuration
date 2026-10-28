#!/usr/bin/env python3
'''
Merges common-ci-configuration's pnpm catalogs and overrides into a
repository's own pnpm-workspace.yaml, for monorepos that cannot symlink ccc's
file (their own `packages:`, `overrides:`, settings or local catalogs).

ccc's catalogs live in a marked region at the top of the consumer's `catalogs:`
mapping, and ccc's overrides in one at the top of its `overrides:` mapping.
Every run rewrites those regions from ccc's file. A local override whose
selector ccc also overrides is removed, so ccc's applies; every other line
(other keys, comments, local catalogs and overrides) is left as it is. A local
catalog named like one of ccc's is an error.

Run it from the consumer, through its ccc submodule:

  python3 <ccc>/scripts/python/pnpm-catalog-sync.py [--check] [WORKSPACE]

WORKSPACE defaults to ./pnpm-workspace.yaml. --check writes nothing; it prints
the diff and exits 1 when the file differs from what a merge would write.
Exit 2: a file can't be read, or its `catalogs:` or `overrides:` block is not
in the style pnpm_catalogs.py reads.
'''
import argparse
import difflib
import sys
from pathlib import Path
from typing import List, Tuple

from pnpm_catalogs import (
    CCC_WORKSPACE, CatalogError, Catalogs, Overrides, find_block, indent_of, is_content, parse_block,
    parse_overrides, read_catalogs, read_overrides, to_yaml,
)

BEGIN = "# BEGIN common-ci-configuration catalogs"
END = "# END common-ci-configuration catalogs"
OVERRIDES_BEGIN = "# BEGIN common-ci-configuration overrides"
OVERRIDES_END = "# END common-ci-configuration overrides"
_MARKERS = {"catalogs": (BEGIN, END), "overrides": (OVERRIDES_BEGIN, OVERRIDES_END)}
_REWRITTEN = "(pnpm-catalog-sync.py rewrites this region from the ccc submodule; do not edit)"


def _region(ccc: Catalogs, indent: int) -> List[str]:
    pad = " " * indent
    lines = [f"{pad}{BEGIN} {_REWRITTEN}\n"]
    for catalog, packages in ccc.items():
        lines.append(f"{pad}{to_yaml(catalog)}:\n")
        lines.extend(f"{pad}{pad}{to_yaml(package)}: {to_yaml(version)}\n" for package, version in packages.items())
    lines.append(f"{pad}{END}\n")
    return lines


def _overrides_region(ccc: Overrides, indent: int) -> List[str]:
    if not ccc:
        return []
    pad = " " * indent
    return ([f"{pad}{OVERRIDES_BEGIN} {_REWRITTEN}\n"]
            + [f"{pad}{to_yaml(selector)}: {to_yaml(version)}\n" for selector, version in ccc.items()]
            + [f"{pad}{OVERRIDES_END}\n"])


def _markers(lines: List[str], start: int, end: int, key: str) -> Tuple[int, int]:
    """(begin index, past-end index) of `key`'s region in lines[start:end]; (start, start) without one."""
    begin, end_marker = _MARKERS[key]
    begins = [i for i, line in enumerate(lines) if line.strip().startswith(begin)]
    ends = [i for i, line in enumerate(lines) if line.strip() == end_marker]
    if not begins and not ends:
        return start, start
    if len(begins) != 1 or len(ends) != 1 or not start <= begins[0] < ends[0] < end:
        raise CatalogError(f"`{key}:` needs one `{begin}` line followed by one `{end_marker}` line, both inside the block")
    return begins[0], ends[0] + 1


def _with_newline(text: str) -> List[str]:
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return lines


def _merge_catalogs(consumer: str, ccc: Catalogs) -> str:
    lines = _with_newline(consumer)
    block = find_block(lines)
    if block is None:
        _markers(lines, 0, 0, "catalogs")
        return "".join(lines) + "catalogs:\n" + "".join(_region(ccc, 2))
    start, end = block
    first, past = _markers(lines, start + 1, end, "catalogs")
    local_lines = [(i + 1, lines[i]) for i in range(start + 1, end) if not first <= i < past]
    shadowed = sorted(set(parse_block(local_lines)) & set(ccc))
    if shadowed:
        raise CatalogError(f"local catalogs {', '.join(shadowed)} are named like ccc catalogs; rename or remove them")
    content = [line for _, line in local_lines if is_content(line)]
    indent = indent_of(content[0]) if content else 2
    return "".join(lines[:first] + _region(ccc, indent) + lines[past:])


def _merge_overrides(consumer: str, ccc: Overrides) -> str:
    lines = _with_newline(consumer)
    block = find_block(lines, "overrides")
    if block is None:
        _markers(lines, 0, 0, "overrides")
        return "".join(lines) + ("overrides:\n" + "".join(_overrides_region(ccc, 2)) if ccc else "")
    start, end = block
    first, past = _markers(lines, start + 1, end, "overrides")
    local = [i for i in range(start + 1, end) if not first <= i < past]
    parse_overrides([(i + 1, lines[i]) for i in local])
    replaced = {i for i in local if is_content(lines[i]) and parse_overrides([(i + 1, lines[i])]).keys() & ccc.keys()}
    content = [lines[i] for i in local if is_content(lines[i])]
    indent = indent_of(content[0]) if content else 2
    before = [lines[i] for i in range(start + 1, first) if i not in replaced]
    after = [lines[i] for i in range(past, end) if i not in replaced]
    return "".join(lines[:start + 1] + before + _overrides_region(ccc, indent) + after + lines[end:])


def merge(consumer: str, ccc: Catalogs, overrides: Overrides) -> str:
    """`consumer` pnpm-workspace.yaml content with its ccc regions holding the catalogs `ccc`
    and the overrides `overrides`, and without its own overrides of the selectors `overrides` sets.

    Raises CatalogError for an unreadable `catalogs:` or `overrides:` block, misplaced
    markers, or a local catalog named like a ccc one.
    """
    return _merge_overrides(_merge_catalogs(consumer, ccc), overrides)


def main() -> int:
    """Merge (or --check) the workspace file named on the command line; return the exit status."""
    parser = argparse.ArgumentParser(description="Merge ccc's pnpm catalogs and overrides into a pnpm-workspace.yaml.")
    parser.add_argument("workspace", nargs="?", default="pnpm-workspace.yaml", type=Path)
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 with a diff when out of sync")
    parser.add_argument("--source", type=Path, default=CCC_WORKSPACE, help="ccc's pnpm-workspace.yaml (default: %(default)s)")
    args = parser.parse_args()
    try:
        current = args.workspace.read_text(encoding="utf-8")
        source = args.source.read_text(encoding="utf-8")
        merged = merge(current, read_catalogs(source), read_overrides(source))
    except (OSError, CatalogError) as error:
        print(f"pnpm-catalog-sync: {args.workspace}: {error}", file=sys.stderr)
        return 2
    if merged == current:
        print(f"{args.workspace}: ccc catalogs and overrides up to date with {args.source}")
        return 0
    if args.check:
        sys.stdout.writelines(difflib.unified_diff(
            current.splitlines(keepends=True), merged.splitlines(keepends=True),
            f"{args.workspace} (committed)", f"{args.workspace} (merged with {args.source})"))
        print(f"{args.workspace} differs from ccc's catalogs and overrides: run `python3 {sys.argv[0]} {args.workspace}`,"
              " refresh pnpm-lock.yaml and commit both", file=sys.stderr)
        return 1
    args.workspace.write_text(merged, encoding="utf-8")
    print(f"{args.workspace}: ccc catalogs and overrides merged from {args.source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
