# archlinux-repo

通过 GitHub Actions 自动构建并发布 Arch Linux 软件包的仓库。每个包的来源和最近记录的版本保存在 `packages/<name>.toml` 中。

## 目录结构

```
packages/
├── fnm-bin.toml
├── dbx-bin.toml
├── kero.toml
├── mygo-cloudmusic.toml
└── bili-fm.toml
.github/
├── scripts/
│   ├── check_updates.py
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
| `url` | AUR 必填 | AUR git 地址 |
| `repo` | GitHub 必填 | GitHub 仓库地址 |
| `method` | GitHub 必填 | `mygo`：源码构建，`mygo build` 生成 PKGBUILD；`prebuilt`：从 release 下载现成的 `pkg.tar.zst` |
| `asset` | 可选 | `prebuilt` 的资产匹配模式，默认 `*.pkg.tar.zst` |
| `tag_prefix` | 可选 | release tag 前缀，默认 `v`；例如 `mygo-v` |

## 构建方式

- **AUR**：`git clone <url>`，在仓库目录执行 `makepkg`。
- **github + mygo**：clone `repo`，执行 `go tool mygo build -platform linux/amd64 -o /build/out`，找到生成的 `PKGBUILD` 并执行 `makepkg`。
- **github + prebuilt**：查询最新 release，下载名称匹配 `asset` 的 `pkg.tar.zst`，直接上传到发布。

> 注意：mygo 源码构建生成的是 `kero`、`mygo-cloudmusic` 包，与 AUR 的 `kero-bin`、`mygo-cloudmusic-bin` 冲突，安装前需先移除对应的 `-bin` 包。

## 自动构建与发布

workflow 在以下情况运行：

- 每天 UTC 00:00 定时检查上游版本
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

包未签名，release 中同时提供 `SHA256SUMS`。构建成功后，workflow 会把实际构建出的版本和 `last_built` 写回对应的 TOML，并以 `[skip ci]` 提交到 `main`。

## 示例

AUR：

```toml
[package]
name = "fnm-bin"
source = "aur"
url = "https://aur.archlinux.org/fnm-bin.git"
version = "1.39.0-1"
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
