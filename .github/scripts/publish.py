#!/usr/bin/env python3
"""Publish built packages to the rolling GitHub Release.

manifest.json is the release's source of truth: it lists every package the
repository currently provides, with its version, architecture, filename,
sha256, size and GitHub asset metadata. Old package assets are deleted after
the manifest is updated, so the release keeps one version per package.
SHA256SUMS is generated from the manifest and therefore covers the whole
release, not only the packages built in this run.
"""

import datetime
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

REPO = os.environ["GITHUB_REPOSITORY"]
TAG = "packages"
DIST = Path("dist")
TITLE = "Arch Linux packages"
NOTES = "Rolling release of the latest built Arch Linux packages."


def run(args, capture=False, check=True):
    result = subprocess.run(args, text=True, capture_output=capture)
    if check and result.returncode != 0:
        if result.stdout:
            sys.stderr.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        raise SystemExit(f"command failed: {' '.join(args)}")
    return result


def gh_json(*args):
    return json.loads(run(["gh", *args], capture=True).stdout)


def release_assets(release_id):
    """Every asset of the release, across API pages."""
    assets = []
    page = 1
    while True:
        batch = gh_json(
            "api",
            f"repos/{REPO}/releases/{release_id}/assets?per_page=100&page={page}",
        )
        assets.extend(batch)
        if len(batch) < 100:
            return assets
        page += 1


def ensure_release():
    result = run(
        ["gh", "api", f"repos/{REPO}/releases/tags/{TAG}"],
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        run([
            "gh", "release", "create", TAG,
            "--repo", REPO,
            "--target", "main",
            "--latest",
            "--title", TITLE,
            "--notes", NOTES,
        ])
        result = run(
            ["gh", "api", f"repos/{REPO}/releases/tags/{TAG}"],
            capture=True,
        )
    release = json.loads(result.stdout)
    return release, release_assets(release["id"])


def read_toml_names():
    names = set()
    for path in sorted(Path("packages").glob("*.toml")):
        with path.open("rb") as fh:
            names.add(tomllib.load(fh)["package"]["name"])
    return names


def read_current():
    """Metadata written by each build job, keyed by pkgname."""
    current = {}
    for path in sorted(DIST.glob("*.json")):
        if path.name == "manifest.json":
            continue
        meta = json.loads(path.read_text())
        for package in meta.get("packages", []):
            entry = dict(package)
            entry["name"] = meta["name"]
            entry["built_at"] = meta.get("built_at", "")
            if meta.get("vcs_revision"):
                entry["vcs_revision"] = meta["vcs_revision"]
            current[entry["pkgname"]] = entry
    return current


def read_previous(assets):
    """The previous manifest, if the release has one."""
    if not any(asset["name"] == "manifest.json" for asset in assets):
        return {}
    run([
        "gh", "release", "download", TAG,
        "--repo", REPO,
        "--pattern", "manifest.json",
        "--dir", str(DIST),
        "--clobber",
    ])
    data = json.loads((DIST / "manifest.json").read_text())
    return {package["pkgname"]: package for package in data.get("packages", [])}


def package_info(path):
    """pkgname, pkgver (with pkgrel) and arch from a package's .PKGINFO."""
    result = run(
        ["bsdtar", "-xOf", str(path), ".PKGINFO"],
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        result = run(
            ["tar", "--zstd", "-xOf", str(path), ".PKGINFO"],
            capture=True,
        )
    text = result.stdout
    fields = {}
    for line in text.splitlines():
        key, sep, value = line.partition(" = ")
        if sep:
            fields[key] = value
    return fields["pkgname"], fields["pkgver"], fields.get("arch", "")


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bootstrap(assets, built_filenames):
    """Adopt assets that predate manifest.json, so cleanup cannot drop them."""
    adopted = {}
    with tempfile.TemporaryDirectory() as tmp:
        for asset in assets:
            name = asset["name"]
            if not name.endswith(".pkg.tar.zst") or name in built_filenames:
                continue
            run([
                "gh", "release", "download", TAG,
                "--repo", REPO,
                "--pattern", name,
                "--dir", tmp,
                "--clobber",
            ])
            path = Path(tmp) / name
            pkgname, version, arch = package_info(path)
            adopted[pkgname] = {
                "name": pkgname,
                "pkgname": pkgname,
                "version": version,
                "arch": arch,
                "filename": name,
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
                "built_at": asset.get("updated_at", ""),
            }
    return adopted


def main():
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    toml_names = read_toml_names()
    current = read_current()

    release, assets = ensure_release()
    previous = read_previous(assets)
    if not previous and assets:
        built_filenames = {package["filename"] for package in current.values()}
        previous = bootstrap(assets, built_filenames)

    # Drop packages whose TOML is gone, then let this run replace what it
    # built. Packages not built this run keep their previous entry.
    merged = {
        pkgname: entry
        for pkgname, entry in previous.items()
        if entry.get("name") in toml_names or entry.get("pkgname") in toml_names
    }
    merged.update(current)

    package_files = sorted(str(path) for path in DIST.glob("*.pkg.tar.zst"))
    if package_files:
        run([
            "gh", "release", "upload", TAG, *package_files,
            "--repo", REPO, "--clobber",
        ])

    _, assets = ensure_release()
    by_name = {asset["name"]: asset for asset in assets}

    packages = {}
    for pkgname, entry in merged.items():
        asset = by_name.get(entry["filename"])
        if asset is None:
            if pkgname in current:
                raise SystemExit(f"{entry['filename']} was not uploaded")
            print(f"warning: {entry['filename']} is missing; dropping it")
            continue
        entry = dict(entry)
        entry["asset_id"] = asset["id"]
        entry["asset_updated_at"] = asset["updated_at"]
        entry["url"] = asset["browser_download_url"]
        entry["size"] = asset["size"]
        packages[pkgname] = entry

    manifest = {
        "generated_at": now,
        "release": TAG,
        "packages": sorted(
            packages.values(), key=lambda package: (package["name"], package["pkgname"])
        ),
    }
    (DIST / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    (DIST / "SHA256SUMS").write_text(
        "".join(
            f"{package['sha256']}  {package['filename']}\n"
            for package in manifest["packages"]
        )
    )

    # Upload the snapshot before deleting old assets, so a reader always
    # sees a manifest that references assets which still exist.
    run([
        "gh", "release", "upload", TAG,
        str(DIST / "manifest.json"), str(DIST / "SHA256SUMS"),
        "--repo", REPO, "--clobber",
    ])

    keep = {package["filename"] for package in manifest["packages"]}
    for asset in assets:
        name = asset["name"]
        if not name.endswith(".pkg.tar.zst") or name in keep:
            continue
        print(f"delete stale asset {name}")
        result = run(
            ["gh", "release", "delete-asset", TAG, name,
             "--repo", REPO, "--yes"],
            check=False,
        )
        if result.returncode != 0:
            print(f"warning: could not delete {name}", file=sys.stderr)

    print(f"{len(manifest['packages'])} package(s) in manifest")
    for package in manifest["packages"]:
        print(
            f"  {package['pkgname']} {package['version']} "
            f"{package['arch']} {package['filename']}"
        )


if __name__ == "__main__":
    main()
