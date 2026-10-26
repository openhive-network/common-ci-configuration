#!/usr/bin/env python3
'''
Writes a package.json that depends on every package of the pnpm catalogs.

Reads the catalogs as `pnpm config get catalogs --json` prints them (run in a
directory holding pnpm-config/pnpm-workspace.yaml) and writes to stdout a
package.json referencing each entry as `catalog:<catalog name>`. Installing that
project fills the pnpm store with every catalog package at its catalog version,
with its dependencies; the emsdk image does this to preload its store.

A package listed in two catalogs is written once when both give the same
version; different versions cannot share one package.json key and are an error.
'''
import json
import sys


def manifest(catalogs):
    """Returns the package.json (as a dict) depending on every catalog entry"""
    dependencies = {}
    versions = {}
    for catalog, packages in sorted(catalogs.items()):
        for package, version in sorted((packages or {}).items()):
            if package in versions and versions[package] != version:
                raise ValueError(
                    f"{package} is {versions[package]} in catalog "
                    f"{dependencies[package][len('catalog:'):]} but {version} in catalog {catalog}")
            if package not in versions:
                versions[package] = version
                dependencies[package] = f"catalog:{catalog}"
    if not dependencies:
        raise ValueError("no catalog entries (is pnpm-workspace.yaml in the current directory?)")
    return {
        "name": "pnpm-catalog-store-preload",
        "private": True,
        "dependencies": dependencies,
    }


def main():
    """Reads catalogs JSON from stdin, writes package.json to stdout"""
    try:
        result = manifest(json.load(sys.stdin))
    except ValueError as error:
        print(f"pnpm-catalog-manifest: {error}", file=sys.stderr)
        return 1
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
