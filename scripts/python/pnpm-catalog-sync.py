#!/usr/bin/env python3
'''
Merges common-ci-configuration's pnpm catalogs into a repository's own
pnpm-workspace.yaml, for monorepos that cannot symlink ccc's file (their own
`packages:`, `overrides:`, settings or local catalogs).

ccc's catalogs live in a marked region at the top of the consumer's `catalogs:`
mapping. Every run rewrites that region from ccc's file and leaves every other
line (other keys, comments, local catalogs) as it is. A local catalog named
like one of ccc's is an error.

Run it from the consumer, through its ccc submodule:

  python3 <ccc>/scripts/python/pnpm-catalog-sync.py [--check] [WORKSPACE]

WORKSPACE defaults to ./pnpm-workspace.yaml. --check writes nothing; it prints
the diff and exits 1 when the file differs from what a merge would write.
Exit 2: a file can't be read, or its `catalogs:` block is not in the style
pnpm_catalogs.py reads.
'''
import argparse
import difflib
import sys
from pathlib import Path
from typing import List, Tuple

from pnpm_catalogs import CCC_WORKSPACE, CatalogError, Catalogs, find_block, indent_of, is_content, parse_block, read_catalogs, to_yaml

BEGIN = "# BEGIN common-ci-configuration catalogs"
END = "# END common-ci-configuration catalogs"
_BEGIN_LINE = f"{BEGIN} (pnpm-catalog-sync.py rewrites this region from the ccc submodule; do not edit)"


def _region(ccc: Catalogs, indent: int) -> List[str]:
    pad = " " * indent
    lines = [f"{pad}{_BEGIN_LINE}\n"]
    for catalog, packages in ccc.items():
        lines.append(f"{pad}{to_yaml(catalog)}:\n")
        lines.extend(f"{pad}{pad}{to_yaml(package)}: {to_yaml(version)}\n" for package, version in packages.items())
    lines.append(f"{pad}{END}\n")
    return lines


def _markers(lines: List[str], start: int, end: int) -> Tuple[int, int]:
    """(BEGIN index, past-END index) of the region in lines[start:end]; (start, start) without one."""
    begins = [i for i, line in enumerate(lines) if line.strip().startswith(BEGIN)]
    ends = [i for i, line in enumerate(lines) if line.strip() == END]
    if not begins and not ends:
        return start, start
    if len(begins) != 1 or len(ends) != 1 or not start <= begins[0] < ends[0] < end:
        raise CatalogError(f"`catalogs:` needs one `{BEGIN}` line followed by one `{END}` line, both inside the block")
    return begins[0], ends[0] + 1


def merge(consumer: str, ccc: Catalogs) -> str:
    """`consumer` pnpm-workspace.yaml content with its ccc region holding the catalogs `ccc`.

    Raises CatalogError for an unreadable `catalogs:` block, misplaced markers, or a
    local catalog named like a ccc one.
    """
    lines = consumer.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    block = find_block(lines)
    if block is None:
        _markers(lines, 0, 0)
        return "".join(lines) + "catalogs:\n" + "".join(_region(ccc, 2))
    start, end = block
    first, past = _markers(lines, start + 1, end)
    local_lines = [(i + 1, lines[i]) for i in range(start + 1, end) if not first <= i < past]
    shadowed = sorted(set(parse_block(local_lines)) & set(ccc))
    if shadowed:
        raise CatalogError(f"local catalogs {', '.join(shadowed)} are named like ccc catalogs; rename or remove them")
    content = [line for _, line in local_lines if is_content(line)]
    indent = indent_of(content[0]) if content else 2
    return "".join(lines[:first] + _region(ccc, indent) + lines[past:])


def main() -> int:
    """Merge (or --check) the workspace file named on the command line; return the exit status."""
    parser = argparse.ArgumentParser(description="Merge ccc's pnpm catalogs into a pnpm-workspace.yaml.")
    parser.add_argument("workspace", nargs="?", default="pnpm-workspace.yaml", type=Path)
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 with a diff when out of sync")
    parser.add_argument("--source", type=Path, default=CCC_WORKSPACE, help="ccc's pnpm-workspace.yaml (default: %(default)s)")
    args = parser.parse_args()
    try:
        current = args.workspace.read_text(encoding="utf-8")
        merged = merge(current, read_catalogs(args.source.read_text(encoding="utf-8")))
    except (OSError, CatalogError) as error:
        print(f"pnpm-catalog-sync: {args.workspace}: {error}", file=sys.stderr)
        return 2
    if merged == current:
        print(f"{args.workspace}: ccc catalogs up to date with {args.source}")
        return 0
    if args.check:
        sys.stdout.writelines(difflib.unified_diff(
            current.splitlines(keepends=True), merged.splitlines(keepends=True),
            f"{args.workspace} (committed)", f"{args.workspace} (merged with {args.source})"))
        print(f"{args.workspace} differs from ccc's catalogs: run `python3 {sys.argv[0]} {args.workspace}`,"
              " refresh pnpm-lock.yaml and commit both", file=sys.stderr)
        return 1
    args.workspace.write_text(merged, encoding="utf-8")
    print(f"{args.workspace}: ccc catalogs merged from {args.source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
