#!/usr/bin/env python3
'''
Prints every package of a pnpm-workspace.yaml's catalogs as `name@version`, one
per line in file order, for preloading them into a pnpm store (the emsdk image
runs `pnpm add $(pnpm-catalog-manifest.py)`).

A package in two catalogs at different versions is an error (exit 1, the
conflicts on stderr): a preload holds one version of it. Exit 2: the file can't
be read or its `catalogs:` block is not in the style pnpm_catalogs.py reads.

Usage: pnpm-catalog-manifest.py [WORKSPACE]   (default: ccc's pnpm-config/pnpm-workspace.yaml)
'''
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from pnpm_catalogs import CCC_WORKSPACE, CatalogError, Catalogs, read_catalogs


def _manifest(catalogs: Catalogs) -> Tuple[List[str], List[str]]:
    first: Dict[str, Tuple[str, str]] = {}
    conflicts = []
    for catalog, packages in catalogs.items():
        for package, version in packages.items():
            seen = first.setdefault(package, (catalog, version))
            if seen[1] != version:
                conflicts.append(f"{package}: {seen[1]} in {seen[0]}, {version} in {catalog}")
    return [f"{package}@{version}" for package, (_, version) in first.items()], conflicts


def main() -> int:
    """Print the manifest of argv[1] (default ccc's catalogs) and return the exit status."""
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else CCC_WORKSPACE
    try:
        packages, conflicts = _manifest(read_catalogs(path.read_text(encoding="utf-8")))
    except (OSError, CatalogError) as error:
        print(f"{path}: {error}", file=sys.stderr)
        return 2
    if conflicts:
        print(f"{path}: packages pinned at different versions by two catalogs:", *conflicts, sep="\n  ", file=sys.stderr)
        return 1
    print(*packages, sep="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
