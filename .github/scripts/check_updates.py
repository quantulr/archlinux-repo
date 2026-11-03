#!/usr/bin/env python3
"""Decide which packages need building by comparing packages/*.toml with upstream.

A package is built when:
  * its TOML has no last_built yet (first run), or
  * the upstream version differs from the recorded version.

Writes the matrix for the build job to $GITHUB_OUTPUT.
"""

import fnmatch
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

AUR_RPC = "https://aur.archlinux.org/rpc/v5/info"
GITHUB_API = "https://api.github.com"
TOKEN = os.environ.get("GITHUB_TOKEN", "")


def warn(message):
    print(f"::warning::{message}")


def gh_get(path):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "archlinux-repo",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(GITHUB_API + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        # GITHUB_TOKEN is scoped to this repository. Other public
        # repositories are readable anonymously when it is not accepted.
        if err.code not in (401, 403) or not TOKEN:
            raise
        req = urllib.request.Request(
            GITHUB_API + path,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "archlinux-repo",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)


def github_slug(url):
    match = re.fullmatch(r"https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?", url)
    if not match:
        raise SystemExit(f"not a GitHub repository URL: {url}")
    return match.group(1)


def strip_prefix(tag, prefix):
    return tag[len(prefix):] if prefix and tag.startswith(prefix) else tag


def repo_version(version):
    """The pkgver of a recorded pkgver-pkgrel version."""
    return version.rsplit("-", 1)[0]


def main():
    matrix = []

    for path in sorted(Path("packages").glob("*.toml")):
        with path.open("rb") as fh:
            pkg = tomllib.load(fh)["package"]

        name = pkg["name"]
        source = pkg["source"]
        recorded = pkg["version"]
        needed = not pkg.get("last_built")

        entry = {
            "name": name,
            "source": source,
            "method": pkg.get("method", ""),
            "url": pkg.get("url", ""),
            "repo": pkg.get("repo", ""),
            "asset": pkg.get("asset", "*.pkg.tar.zst"),
            "asset_url": "",
            "asset_name": "",
            "tag": "",
            "upstream": "",
        }

        if source == "aur":
            query = urllib.parse.quote(name, safe="")
            with urllib.request.urlopen(f"{AUR_RPC}?arg[]={query}", timeout=30) as resp:
                results = json.load(resp).get("results") or []
            if not results:
                warn(f"{name}: not found on AUR, skipped")
                continue
            upstream = results[0]["Version"]
            entry["upstream"] = upstream
            if upstream != recorded:
                needed = True

        elif source == "github":
            method = pkg.get("method")
            if method not in ("mygo", "prebuilt"):
                raise SystemExit(f"{path}: unknown method {method!r}")
            slug = github_slug(pkg["repo"])
            prefix = pkg.get("tag_prefix", "v")

            try:
                release = gh_get(f"/repos/{slug}/releases/latest")
            except urllib.error.HTTPError as err:
                if err.code != 404:
                    raise
                release = None

            if release:
                entry["tag"] = release["tag_name"]
            else:
                tags = gh_get(f"/repos/{slug}/tags")
                if not tags:
                    warn(f"{name}: no release or tag found, skipped")
                    continue
                entry["tag"] = tags[0]["name"]
                if method == "prebuilt":
                    warn(f"{name}: no release to download a prebuilt package from, skipped")
                    continue

            upstream = strip_prefix(entry["tag"], prefix)
            entry["upstream"] = upstream
            if upstream != repo_version(recorded):
                needed = True

            if method == "prebuilt":
                asset = entry["asset"]
                matches = [
                    a
                    for a in release.get("assets", [])
                    if fnmatch.fnmatch(a["name"], asset)
                ]
                if not matches:
                    warn(f"{name}: {entry['tag']} has no asset matching {asset}, skipped")
                    continue
                if len(matches) > 1:
                    warn(
                        f"{name}: {len(matches)} assets match {asset}, "
                        f"using {matches[0]['name']}"
                    )
                entry["asset_url"] = matches[0]["browser_download_url"]
                entry["asset_name"] = matches[0]["name"]

        else:
            raise SystemExit(f"{path}: unknown source {source!r}")

        if needed:
            matrix.append(entry)
            print(f"build {name}: recorded {recorded}, upstream {entry['upstream']}")
        else:
            print(f"skip  {name}: recorded {recorded} is current")

    with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
        fh.write("matrix=" + json.dumps(matrix, separators=(",", ":")) + "\n")
        fh.write(f"count={len(matrix)}\n")

    print(f"{len(matrix)} package(s) to build")


if __name__ == "__main__":
    main()
