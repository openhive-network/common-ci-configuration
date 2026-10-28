#!/usr/bin/env python3
'''
Merges common-ci-configuration's pnpm catalogs into a repository's own
pnpm-workspace.yaml, for monorepos that cannot symlink ccc's file (their own
`packages:`, `overrides:`, settings or local catalogs).

ccc's catalogs go into a marked region at the top of the consumer's `catalogs:`
mapping; that region is rewritten from ccc's file on every run, and everything
outside it (other keys, comments, local catalogs) is left byte for byte. A local
catalog named like one of ccc's is an error.

Run it from the consumer, through its ccc submodule:

  python3 <ccc>/scripts/python/pnpm-catalog-sync.py [--check] [WORKSPACE]

WORKSPACE defaults to ./pnpm-workspace.yaml. --check writes nothing and exits 1,
printing the diff, when the file differs from what a merge would write.
Exit 2: either file has a `catalogs:` block this script cannot read.
'''
import argparse
import difflib
import sys
from pathlib import Path
from typing import List

from pnpm_catalogs import DEFAULT_WORKSPACE, CatalogError, Catalogs, catalogs_span, is_content, parse_block, parse_catalogs, quote

BEGIN = "# BEGIN common-ci-configuration catalogs"
END = "# END common-ci-configuration catalogs"
_BEGIN_LINE = BEGIN + ": written by pnpm-catalog-sync.py from the ccc submodule, do not edit"


def _region(catalogs: Catalogs, indent: int) -> List[str]:
    pad = " " * indent
    lines = [f"{pad}{_BEGIN_LINE}\n"]
    for catalog, packages in catalogs.items():
        lines.append(f"{pad}{quote(catalog)}:\n")
        lines.extend(f"{pad * 2}{quote(package)}: {quote(version)}\n" for package, version in packages.items())
    lines.append(f"{pad}{END}\n")
    return lines


def _marker_lines(lines: List[str], start: int, end: int) -> List[int]:
    """Indexes of the BEGIN and END markers in lines[start:end] ([] without them)."""
    begins = [i for i in range(start, end) if lines[i].strip().startswith(BEGIN)]
    ends = [i for i in range(start, end) if lines[i].strip() == END]
    if not begins and not ends:
        return []
    if len(begins) != 1 or len(ends) != 1 or begins[0] > ends[0]:
        raise CatalogError(f"`catalogs:` needs exactly one `{BEGIN}` line followed by one `{END}` line")
    return [begins[0], ends[0]]


def merge(consumer: str, ccc: Catalogs) -> str:
    """`consumer` pnpm-workspace.yaml content with its ccc region holding `ccc`'s catalogs."""
    lines = consumer.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    span = catalogs_span(lines)
    if span is None:
        return "".join(lines) + "catalogs:\n" + "".join(_region(ccc, 2))
    start, end = span
    if any(lines[i].strip().startswith(("# BEGIN common-ci", "# END common-ci")) for i in range(len(lines)) if not start < i < end):
        raise CatalogError("the ccc catalog markers must be inside the `catalogs:` block")
    markers = _marker_lines(lines, start + 1, end)
    region = range(markers[0], markers[1] + 1) if markers else range(0)
    local_lines = [line for i, line in enumerate(lines[:end]) if i not in region]
    local = parse_block(local_lines, start + 1, len(local_lines))
    shadowed = sorted(set(local) & set(ccc))
    if shadowed:
        raise CatalogError(f"local catalogs {', '.join(shadowed)} have the names of ccc catalogs; rename or remove them")
    content = [line for line in local_lines[start + 1:] if is_content(line)]
    indent = len(content[0]) - len(content[0].lstrip(" ")) if content else 2
    at = markers[0] if markers else start + 1
    return "".join(lines[:at] + _region(ccc, indent) + lines[region.stop if markers else at:])


def main() -> int:
    """Merge (or --check) argv's workspace file against ccc's catalogs."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0].strip())
    parser.add_argument("workspace", nargs="?", default="pnpm-workspace.yaml", type=Path)
    parser.add_argument("--check", action="store_true", help="exit 1 with a diff if the file is out of sync")
    parser.add_argument("--source", type=Path, default=DEFAULT_WORKSPACE, help="ccc's pnpm-workspace.yaml")
    args = parser.parse_args()
    try:
        ccc = parse_catalogs(args.source.read_text(encoding="utf-8"))
        current = args.workspace.read_text(encoding="utf-8")
        merged = merge(current, ccc)
    except (OSError, CatalogError) as error:
        print(f"pnpm-catalog-sync: {error}", file=sys.stderr)
        return 2
    if merged == current:
        print(f"{args.workspace}: ccc catalogs up to date ({args.source})")
        return 0
    if args.check:
        sys.stdout.writelines(difflib.unified_diff(
            current.splitlines(keepends=True), merged.splitlines(keepends=True),
            str(args.workspace), f"{args.workspace} (merged with {args.source})"))
        print(f"{args.workspace} differs from ccc's catalogs ({args.source}): run"
              f" `python3 {sys.argv[0]} {args.workspace}` and commit it with the refreshed pnpm-lock.yaml", file=sys.stderr)
        return 1
    args.workspace.write_text(merged, encoding="utf-8")
    print(f"{args.workspace}: ccc catalogs merged from {args.source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
