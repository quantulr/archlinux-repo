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
    info = json.loads(meta.read_text())
    path = Path("packages") / f"{info['name']}.toml"
    if not path.exists():
        raise SystemExit(f"{meta}: no TOML for package {info['name']!r}")

    text = path.read_text()
    text, count = re.subn(
        r'(?m)^version = ".*"$',
        f"version = {json.dumps(info['version'])}",
        text,
        count=1,
    )
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one version line")

    if re.search(r"(?m)^last_built = ", text):
        text = re.sub(
            r'(?m)^last_built = ".*"$',
            f"last_built = {json.dumps(now)}",
            text,
            count=1,
        )
    else:
        text = text.rstrip() + f"\nlast_built = {json.dumps(now)}\n"

    path.write_text(text)
    print(f"{path}: version {info['version']}, last_built {now}")
