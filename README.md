# archlinux-repo

通过 GitHub Actions 自动构建并发布 Arch Linux 软件包的仓库。每个包的来源和最近记录的版本保存在 `packages/<name>.toml` 中。

## 目录结构

```
packages/
├── bili-fm.toml
├── claude-code.toml
├── crush.toml
├── dbx-bin.toml
├── fcitx5-qingjian-git.toml
├── fselect.toml
├── kero.toml
├── mygit.toml
├── umbriel-git.toml
└── xdg-desktop-portal-umbriel-git.toml
.github/
├── scripts/
│   ├── check_updates.py
│   ├── publish.py
│   └── record_versions.py
└── workflows/
    └── build.yml
```

## TOML 格式

所有字段位于 `[package]` 表：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `name` | 是 | 包名，必须与文件名（不含 `.toml`）一致 |
| `source` | 是 | 来源类型：`aur` 或 `github` |
| `version` | 是 | 最近记录的版本，格式 `pkgver-pkgrel`；构建成功后由 workflow 回写 |
| `last_built` | 可选 | 最近一次成功构建的时间（ISO 8601），由 workflow 写入；缺失时会强制构建 |
| `vcs_revision` | 可选 | `-git` AUR 包最近构建的上游 commit；由 workflow 写入 |
| `upstream_version` | 可选 | GitHub 包最近构建对应的上游 release tag（去掉 `tag_prefix`）；由 workflow 写入 |
| `url` | AUR 必填 | AUR git 地址 |
| `repo` | GitHub 必填 | GitHub 仓库地址 |
| `method` | GitHub 必填 | `mygo`：源码构建，`mygo build` 生成 PKGBUILD；`prebuilt`：从 release 下载现成的 `pkg.tar.zst` |
| `asset` | 可选 | `prebuilt` 的资产匹配模式，默认 `*.pkg.tar.zst` |
| `tag_prefix` | 可选 | release tag 前缀，默认 `v`；例如 `mygo-v` |

## 构建方式

- **AUR**：`git clone <url>`，安装 `depends`/`makedepends` 中属于官方仓库的包后执行 `makepkg`；AUR 内部依赖在构建时跳过，运行时由本仓库提供。
- **github + mygo**：clone `repo`，执行 `go tool mygo build -platform linux/amd64 -o /build/out`，找到生成的 `PKGBUILD` 并执行 `makepkg`。项目锁定的 mygo 必须 ≥ v0.7.4 才能生成 PKGBUILD。
- **github + prebuilt**：查询最新 release，下载名称匹配 `asset` 的 `pkg.tar.zst`，直接上传到发布。

> 注意：`kero` 由 mygo 源码构建生成，与 AUR 的 `kero-bin` 冲突，安装前需先移除 `kero-bin`。

### `-git` AUR 包

AUR RPC 返回的 `version` 来自 `.SRCINFO`，不会随上游新 commit 变化。对名称以 `-git` 结尾的 AUR 包，workflow 会：

1. 克隆 AUR 仓库并解析 `.SRCINFO` 中第一个 `git+` 源；
2. 用 `git ls-remote` 获取该源最新 commit；
3. 与 TOML 中的 `vcs_revision` 比较，不同则构建。

首次构建或缺少 `vcs_revision` 时会强制构建，成功后写回 `version` 和 `vcs_revision`。

## 自动构建与发布

workflow 在以下情况运行：

- 每小时（UTC 整点）定时检查上游版本
- 手动触发（Actions → Build packages → Run workflow）
- `packages/**`、`.github/scripts/**` 或 workflow 文件变更时

`prepare` 任务比较 TOML 中的 `version` 与上游最新版本；缺少 `last_built` 时也会强制构建。构建成功的包上传到 tag 为 `packages` 的滚动 GitHub Release：

```
https://github.com/quantulr/archlinux-repo/releases/tag/packages
```

下载并安装：

```sh
sudo pacman -U https://github.com/quantulr/archlinux-repo/releases/download/packages/<文件名>.pkg.tar.zst
```

包未签名。构建成功后，workflow 会把实际构建出的版本和 `last_built` 写回对应的 TOML，并以 `[skip ci]` 提交到 `main`。

### Release 资产

- `manifest.json`：release 的完整快照，列出每个包的 `name`、`pkgname`、`version`、`arch`、`filename`、`sha256`、`size`、`built_at`、`asset_id`、`asset_updated_at`、`url`。本地同步程序应以它为准：
  `https://github.com/quantulr/archlinux-repo/releases/download/packages/manifest.json`
- `SHA256SUMS`：由 manifest 生成，覆盖 release 中所有包。
- `*.pkg.tar.zst`：每个包只保留最新版本；更新后不再被 manifest 引用的旧资产会自动删除，因此 release 不保留历史版本（版本历史仍可在 git 中查看）。带 epoch 的包文件名中的 `:` 会替换为 `_` 以兼容 GitHub Artifacts，版本仍以包内 `.PKGINFO` 为准。
- 从 `packages/` 删除 TOML 后，下一次运行会检测到 manifest 中多出的包，即使没有新的构建也会运行 publish，自动更新 manifest/SHA256SUMS 并删除对应资产。

## 示例

AUR：

```toml
[package]
name = "claude-code"
source = "aur"
url = "https://aur.archlinux.org/claude-code.git"
version = "2.1.317-1"
```

GitHub mygo 源码构建：

```toml
[package]
name = "kero"
source = "github"
method = "mygo"
repo = "https://github.com/egoist/kero"
version = "2.1.31-1"
```

GitHub release 直取：

```toml
[package]
name = "bili-fm"
source = "github"
method = "prebuilt"
repo = "https://github.com/vst93/bili-fm"
asset = "*-linux-x86_64.pkg.tar.zst"
tag_prefix = "mygo-v"
version = "3.1.16-1"
```
