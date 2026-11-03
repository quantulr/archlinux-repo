#!/usr/bin/env python3
"""Write each built package's version and build time back into its TOML.

Reads the metadata files the build job uploads (dist/<name>.json) and
updates packages/<name>.toml in place, keeping the rest of the file as is.
"""

import datetime
import json
import re
from pathlib import Path

now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

for meta in sorted(Path("dist").glob("*.json")):
    if meta.name == "manifest.json":
        continue
    info = json.loads(meta.read_text())
    packages = info.get("packages") or []
    if not packages:
        continue

    path = Path("packages") / f"{info['name']}.toml"
    if not path.exists():
        raise SystemExit(f"{meta}: no TOML for package {info['name']!r}")

    version = packages[0]["version"]
    built_at = info.get("built_at") or now

    text = path.read_text()
    text, count = re.subn(
        r'(?m)^version = ".*"$',
        f"version = {json.dumps(version)}",
        text,
        count=1,
    )
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one version line")

    if re.search(r"(?m)^last_built = ", text):
        text = re.sub(
            r'(?m)^last_built = ".*"$',
            f"last_built = {json.dumps(built_at)}",
            text,
            count=1,
        )
    else:
        text = text.rstrip() + f"\nlast_built = {json.dumps(built_at)}\n"

    vcs_revision = info.get("vcs_revision")
    if vcs_revision:
        if re.search(r"(?m)^vcs_revision = ", text):
            text = re.sub(
                r'(?m)^vcs_revision = ".*"$',
                f"vcs_revision = {json.dumps(vcs_revision)}",
                text,
                count=1,
            )
        else:
            text = text.rstrip() + f"\nvcs_revision = {json.dumps(vcs_revision)}\n"

    path.write_text(text)
    suffix = f", vcs_revision {vcs_revision}" if vcs_revision else ""
    print(f"{path}: version {version}, last_built {built_at}{suffix}")
