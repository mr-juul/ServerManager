from __future__ import annotations

import argparse
import re
from pathlib import Path

VERSION_FILE = Path("core/version.py")


def normalize_tag(tag: str) -> str:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", tag)
    if not match:
        raise ValueError(f"Could not parse semantic version from tag: {tag}")
    return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"


def sync_version(tag: str) -> str:
    version = normalize_tag(tag)
    original = VERSION_FILE.read_text(encoding="utf-8")
    lines = original.splitlines()
    updated_lines = []
    replaced = False
    for line in lines:
        if line.startswith("APP_VERSION = "):
            updated_lines.append(f'APP_VERSION = "{version}"')
            replaced = True
        else:
            updated_lines.append(line)

    if not replaced:
        raise RuntimeError("APP_VERSION was not found in core/version.py")

    updated = "\n".join(updated_lines) + "\n"
    if updated != original:
        VERSION_FILE.write_text(updated, encoding="utf-8")
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync APP_VERSION from git tag")
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()

    version = sync_version(args.tag)
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
