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
import subprocess
import sys
import tempfile
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


def vcs_source(aur_url):
    """The git URL and ref of an AUR package's first git+ source, if any."""
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            ["git", "clone", "--quiet", "--depth", "1", aur_url, tmp],
            text=True,
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            return None
        srcinfo = Path(tmp) / ".SRCINFO"
        if not srcinfo.exists():
            return None
        for line in srcinfo.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line.startswith("source"):
                continue
            _, sep, value = line.partition(" = ")
            if not sep:
                continue
            if "::" in value:
                value = value.split("::", 1)[1]
            if not value.startswith("git+"):
                continue
            value = value[len("git+"):]
            url, _, fragment = value.partition("#")
            url = url.split("?", 1)[0]
            ref = "HEAD"
            if fragment.startswith("branch="):
                ref = "refs/heads/" + fragment[len("branch="):]
            elif fragment.startswith("tag="):
                ref = "refs/tags/" + fragment[len("tag="):]
            return url, ref
    return None


def latest_vcs_revision(url, ref):
    """The commit the ref points to, or "" when it cannot be queried."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", url, ref],
            text=True,
            capture_output=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return ""
    if result.returncode != 0:
        return ""
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return lines[0].split()[0] if lines else ""


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

            # AUR's version of a -git package is the pkgver in .SRCINFO,
            # which does not move with upstream commits. Compare the
            # upstream VCS revision instead.
            vcs_handled = False
            if name.endswith("-git"):
                aur_url = pkg.get("url") or f"https://aur.archlinux.org/{name}.git"
                try:
                    source_info = vcs_source(aur_url)
                except (subprocess.TimeoutExpired, OSError) as err:
                    warn(f"{name}: could not read the AUR repo: {err}")
                    source_info = None
                if source_info:
                    url, ref = source_info
                    latest = latest_vcs_revision(url, ref)
                    if latest:
                        vcs_handled = True
                        entry["upstream"] = latest
                        recorded_rev = pkg.get("vcs_revision", "")
                        if (
                            not pkg.get("last_built")
                            or not recorded_rev
                            or latest != recorded_rev
                        ):
                            needed = True
                    else:
                        warn(
                            f"{name}: could not query {url}; "
                            "falling back to the AUR version"
                        )

            if not vcs_handled:
                if name.endswith("-git") and pkg.get("last_built"):
                    # AUR's version does not track upstream commits; a
                    # version comparison here would rebuild every run.
                    warn(f"{name}: could not resolve the VCS source; skipping this run")
                    continue
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
