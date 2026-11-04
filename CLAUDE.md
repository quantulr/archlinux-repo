# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

这是一个 GitHub Actions 驱动的 Arch Linux 二进制包仓库。`packages/<name>.toml` 是每个包的唯一配置与状态文件；workflow 检查上游版本、在 Arch 容器中构建、把 `.pkg.tar.zst` 上传到滚动 GitHub Release（tag `packages`），并把实际构建结果写回 TOML。

手动构建/发布不是常规工作流；改动通常落在 `packages/*.toml`、`.github/workflows/build.yml` 或 `.github/scripts/*.py`。字段定义见 `README.md`。

## 常用命令

### 校验

```sh
go run github.com/rhysd/actionlint/cmd/actionlint@latest .github/workflows/build.yml
python3 -m py_compile .github/scripts/check_updates.py .github/scripts/publish.py .github/scripts/record_versions.py
```

没有单元测试；验证主要靠 actionlint、`py_compile` 和实际运行 workflow。

### 本地运行更新检查（只读）

```sh
out=$(mktemp)
GITHUB_REPOSITORY=quantulr/archlinux-repo \
GITHUB_TOKEN=$(gh auth token) \
GITHUB_OUTPUT="$out" \
python3 .github/scripts/check_updates.py
cat "$out"     # matrix=... / count=... / reconcile=...
```

- `GITHUB_REPOSITORY` 用于 reconcile（读取 release 的 manifest）；不设置时跳过该检查。
- `GITHUB_OUTPUT` 必须指向可写文件。
- 脚本会访问 AUR RPC、GitHub API，并对 `-git` AUR 包 clone AUR 仓库、执行 `git ls-remote`。

### 触发与观察 workflow

```sh
gh workflow run build.yml --ref main
gh run list --workflow build.yml --limit 5
gh run watch <run-id> --exit-status
```

新增 workflow 文件的那次 push 不一定会触发它；可用 `gh workflow run` 手动启动。

### 本地构建单个包

AUR（在 Arch 上以非 root 用户执行；先按 `.SRCINFO` 安装官方仓库依赖）：

```sh
git clone --depth 1 https://aur.archlinux.org/<pkg>.git /tmp/aur-<pkg>
cd /tmp/aur-<pkg>
makepkg -f --noconfirm --nodeps --nocheck
```

mygo：

```sh
git clone --depth 1 --branch <tag> https://github.com/<owner>/<repo> /tmp/mygo-<pkg>
cd /tmp/mygo-<pkg>
go tool mygo build -platform linux/amd64 -o /tmp/out
# 在 /tmp/out/linux-amd64/ 找到生成的 PKGBUILD，再执行 makepkg -f --noconfirm --nodeps
```

项目锁定的 mygo 必须 ≥ v0.7.4 才会生成 PKGBUILD；否则 workflow 会报 `mygo build generated no PKGBUILD`。

### 检查 Release

```sh
gh release view packages --repo quantulr/archlinux-repo --json assets
d=$(mktemp -d)
gh release download packages --repo quantulr/archlinux-repo -p manifest.json -D "$d" --clobber
```

## 架构

### 数据流

```
packages/*.toml
  → prepare（check_updates.py）→ matrix / count / reconcile
  → build（Arch 容器，矩阵并行）→ artifacts
  → publish（publish.py）→ Release `packages`
  → record_versions.py → 提交 version / last_built / vcs_revision 到 main（[skip ci]）
```

### prepare（`.github/scripts/check_updates.py`）

- 触发：每小时 UTC 整点的 `schedule`、`workflow_dispatch`、以及 main 上 `packages/**`、`.github/scripts/**`、workflow 文件的 push。
- 仅当 `github.ref == refs/heads/main` 时运行。
- AUR 非 `-git`：AUR RPC 的 `Version` 与 TOML `version` 比较。
- AUR `-git`：AUR RPC 版本不跟随上游 commit，因此 clone AUR 仓库、解析 `.SRCINFO` 第一个 `git+` 源、`git ls-remote` 取最新 commit，与 TOML 的 `vcs_revision` 比较。VCS 源解析失败且已有 `last_built` 时跳过本轮，避免每小时误重建。
- GitHub `mygo` / `prebuilt`：查询最新 release，tag 去掉 `tag_prefix`（默认 `v`）后与 `version` 的 pkgver 比较；`prebuilt` 还需匹配 `asset` glob，并输出资产的下载 URL。
- `last_built` 缺失 ⇒ 强制构建。
- 输出 `matrix`、`count`、`reconcile`。`reconcile=true` 表示 release manifest 中存在已从 `packages/` 删除的包。

### build（`.github/workflows/build.yml`）

- 仅 `count != 0` 时运行；`archlinux:base-devel` 容器，`fail-fast: false`。
- AUR：从 `.SRCINFO` 解析 `depends`/`makedepends`/`checkdepends`，只安装官方仓库中存在的（`cargo`/`rustc` 映射为 `rust`）；AUR-only 依赖跳过（因此用 `--nodeps`）。从 PKGBUILD 的 `validpgpkeys` 导入 GPG key 到 builder keyring，导入失败才加 `--skippgpcheck`。以 builder 用户执行 `makepkg -f --noconfirm --nodeps --nocheck`。
- mygo：clone prepare 给出的 tag → `go tool mygo build -platform linux/amd64 -o /build/out` → 必须找到生成的 PKGBUILD → `makepkg`。
- prebuilt：直接 `curl` prepare 给出的 `asset_url`，不构建。
- 排除 `*-debug-*.pkg.tar.zst`；文件名中的 `:` 替换为 `_`（GitHub Artifacts 不允许冒号）。
- 每个包输出 `<name>.json` 元数据（`name`、`built_at`、可选 `vcs_revision`，以及 `packages` 数组中的 `pkgname`/`version`/`arch`/`filename`/`sha256`/`size`），上传 artifact `pkg-<name>`。
- `vcs_revision` 只对包名以 `-git` 结尾的包记录。

### publish（`.github/scripts/publish.py`）

- 运行条件：build 成功，或 build 被跳过且 `reconcile=true`。build 失败时不发布。
- reconcile 运行没有 artifact 下载步骤；`publish.py` 会自行创建 `dist`。
- `manifest.json` 是 release 的唯一事实来源：
  - 读取旧 manifest 与本次构建元数据，按 `pkgname` 合并；
  - 丢掉已不在 `packages/` 的包；
  - release 没有 manifest 时 bootstrap：下载现有资产并用 `.PKGINFO` 识别，避免清理误删。
- 上传新包资产（`--clobber`）→ 生成完整 `manifest.json` 与完整 `SHA256SUMS` → 先上传 manifest/checksums，再删除不再被引用的 `.pkg.tar.zst`（每包只保留最新版本，debug 包也会被清理）。
- manifest 条目：`name`、`pkgname`、`version`、`arch`、`filename`、`sha256`、`size`、`built_at`、`asset_id`、`asset_updated_at`、`url`；`-git` 包额外有 `vcs_revision`。
- `record_versions.py` 把本次构建的 `version`、`last_built`、`vcs_revision` 写回 TOML；workflow 以 `chore(packages): record built versions [skip ci]` 提交到 main。
- Release 未签名；本地 pacman 使用 `SigLevel = Optional TrustAll` 或 `Never`。

## 修改时的关键约束

- `packages/<name>.toml` 的 `name` 必须与文件名一致；`record_versions.py` 按这个 `name` 找到对应 TOML 并回写。
- `version`、`last_built`、`vcs_revision` 是 workflow 管理的状态字段。删除 `last_built` 会强制下一次构建；不要手工把它们改成未实际构建的值。
- `-git` AUR 包不要改回用 AUR RPC 的 `version` 判断更新，必须使用 `vcs_revision`。
- 删除 `packages/*.toml` 后，依赖 `prepare` 的 reconcile 在下一轮清理 release；不要假设删文件本身会立即删除资产。
- AUR 构建只安装官方仓库依赖；AUR-only 运行时依赖（例如 `umbriel-git` 依赖 `xdg-desktop-portal-umbriel-git`）必须先在本仓库中构建，否则用户安装时缺依赖。
- 新增 GitHub `mygo` 包前确认其锁定的 mygo ≥ v0.7.4；当前 workflow 不会自动升级项目里的 mygo 工具。
- 修改 workflow 后用 actionlint 校验；修改 Python 脚本后用 `py_compile`。`publish.py` 会修改 release，`record_versions.py` 会修改 TOML，本地运行前确认意图。
- `GITHUB_TOKEN` 推送的提交不会触发新 workflow；版本回写提交也带 `[skip ci]`，避免循环。
- 定时任务可能被 GitHub 延迟；`workflow_dispatch` 若从非 main 分支触发，`prepare` 会因 `github.ref` 条件被跳过。
