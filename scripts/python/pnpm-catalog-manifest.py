#!/usr/bin/env python3
'''
Prints every package of a pnpm-workspace.yaml's catalogs as `name@version`, one
per line in catalog order, for preloading them into a pnpm store (the emsdk image
does `pnpm add $(pnpm-catalog-manifest.py)`).

A package listed in several catalogs at different versions is an error (exit 1,
the conflicts on stderr): a preload holds one version of each package.

Usage: pnpm-catalog-manifest.py [WORKSPACE]   (default: ccc's pnpm-config/pnpm-workspace.yaml)
'''
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from pnpm_catalogs import DEFAULT_WORKSPACE, CatalogError, Catalogs, parse_catalogs


def manifest(catalogs: Catalogs) -> Tuple[List[str], List[str]]:
    """(`name@version` per distinct package, a message per conflicting package)."""
    seen: Dict[str, Tuple[str, str]] = {}
    conflicts = []
    for catalog, packages in catalogs.items():
        for package, version in packages.items():
            if package not in seen:
                seen[package] = (catalog, version)
            elif seen[package][1] != version:
                first_catalog, first_version = seen[package]
                conflicts.append(f"{package}: {first_catalog} has {first_version}, {catalog} has {version}")
    return [f"{package}@{version}" for package, (_, version) in seen.items()], conflicts


def main() -> int:
    """Print the manifest of argv[1] (or ccc's catalog); 1 on conflicts, 2 on an unreadable file."""
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_WORKSPACE
    try:
        packages, conflicts = manifest(parse_catalogs(path.read_text(encoding="utf-8")))
    except (OSError, CatalogError) as error:
        print(f"{path}: {error}", file=sys.stderr)
        return 2
    if conflicts:
        print(f"{path}: packages with conflicting catalog versions:", *conflicts, sep="\n  ", file=sys.stderr)
        return 1
    print(*packages, sep="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
