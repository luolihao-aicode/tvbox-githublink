# IPTV 播放源管理工具

一个使用 Python 开发的开源 IPTV 播放源管理工具：自动发现公开列表、检测真实播放能力、记录最近 30 次结果、选择高速稳定高清的主备线路，并生成电视播放器可以直接订阅的 M3U 文件。

## 功能

- 从配置种子和 GitHub API 发现公开 M3U/M3U8 列表。
- 异步检查 HTTP 状态、首字节延迟、HLS 清单和媒体分片。
- 使用 `ffprobe` 验证视频轨道、分辨率、编码、码率和持续出流。
- 按可用率、启动速度、清晰度、码率和连续读取稳定性评分。
- 自动执行主备线路选择、冷却、恢复和长期失效清理。
- 频道分类为央视、地方频道、香港和台湾；地方频道仅含主流省级卫视。
- 每日自动检测、每周自动发现、自动提交结果并部署 GitHub Pages。
- 不加载频道 Logo，状态页使用原生 HTML/CSS/JavaScript。

## 环境要求

- Python 3.12 或更高版本。
- FFmpeg（需要可执行命令 `ffprobe`）。
- Git（需要提交自动生成结果时）。

## 本地运行

### Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

请通过 FFmpeg 官网或 Windows 包管理器安装 FFmpeg，并确认：

```powershell
ffprobe -version
```

### macOS

```bash
brew install python@3.12 ffmpeg
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

### Linux（Ubuntu/Debian）

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv ffmpeg
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

执行测试和试运行：

```bash
python -m pytest -q
iptv-manager update --config-dir config --data-dir data --output-dir output --dry-run
```

执行真实更新：

```bash
iptv-manager update --config-dir config --data-dir data --output-dir output
```

搜索 GitHub 新来源需要环境变量 `GITHUB_TOKEN`：

```bash
iptv-manager discover --config-dir config --data-dir data --output-dir output
```

## 配置

- `config/sources.yml`：公开种子地址、来源类型、大小限制及 GitHub 搜索词。
- `config/channels.yml`：允许发布的频道、标准名称、分类和别名。
- `config/policy.yml`：超时、并发、最低清晰度、历史长度和切换阈值。

新发现的源不会立即发布。它必须通过 HTTP 和 `ffprobe` 检测，并至少连续成功两次。默认最低清晰度为 720p；只有单频道显式配置后才允许降级到 540p。

默认已配置 iptv-org 的中国大陆、香港、台湾三个公开社区列表，日常检测不依赖 GitHub 代码搜索是否可用。列表里的英文名称和分辨率注释会映射到频道白名单；发现了线路并不保证该线路通过播放与高清检测。

台湾补充来源包括 iptv-org 原始台湾列表和两份公开社区列表，仅收录台湾频道。频道别名兼容简体、繁体及明确的中英文混合名称。可单独复检台湾线路，保留其他分类已有检测记录：

```bash
iptv-manager update --category 台湾 --config-dir config --data-dir data --output-dir output
```

媒体复检默认最多并发 4 个进程，且受单域名并发限制；可在 `policy.yml` 中调整 `media_concurrency`。

## 检测指标

综合评分为 100 分：

HTTP 可访问和媒体可解码只证明传输可用，不证明正在播放目标频道。检测会拒绝派生地址中的已知无信号占位视频，以及包含 `#EXT-X-ENDLIST` 或 VOD 标记的非直播清单。人工确认错标或长时间解码不稳定的线路可在 `policy.yml` 的 `rejected_streams` 中按稳定线路 ID 禁用，避免历史成功记录使它再次进入订阅。这不是自动识别所有广告或频道标志，未知占位内容仍需人工核验。

- 最近 30 次可用率：40%。
- 启动速度：25%。
- 清晰度与码率：20%。
- 连续读取稳定性：15%。

主源正常时，备用源需要高出至少 8 分才允许替换，避免线路因短暂波动频繁切换。网页显示的是 GitHub Actions 节点测得的结果，不代表用户本地网络、运营商或电视端的真实速度。

## 订阅地址

部署到 GitHub Pages 后，假设仓库为 `用户名/仓库名`，订阅地址格式为：

```text
https://用户名.github.io/仓库名/combined.m3u
https://用户名.github.io/仓库名/official.m3u
https://用户名.github.io/仓库名/community.m3u
https://用户名.github.io/仓库名/backup.m3u
https://用户名.github.io/仓库名/taiwan.m3u
https://用户名.github.io/仓库名/taiwan-libretv.m3u
```

- `combined.m3u`：普通用户推荐，每频道一个最佳线路，合格时优先官方源。
- `official.m3u`：仅官方来源。
- `community.m3u`：检测合格的社区发现来源。
- `backup.m3u`：主源及最多两个备用源。
- `taiwan.m3u`：仅台湾频道，包含合格主源与备用源，便于在电视上逐条试播；采用同样的高清与连续两次成功门槛。
- `taiwan-libretv.m3u`：台湾 HLS 兼容子集，仅导出明确以 `.m3u8` 结尾的地址（允许查询参数），自动从合格备用线路补位，不降低高清门槛。未包含的原始 TS 直链可在 VLC、PotPlayer 或支持该格式的电视播放器中试播。

### LibreTV 试播

在“设置 → 源管理 → 直播源”添加 `taiwan-libretv.m3u` 的完整订阅地址，停用旧台湾测试订阅，避免同名旧线路混入。回到直播页，清除旧测活结果，再选择台湾分组试播。

[LibreTV 官方直播说明](https://github.com/LibreSpark/LibreTV/wiki/Live-IPTV)中，绿点表示分片级可达，黄点表示弱验证或超时，并不表示实际播放流畅或节目内容正确；测活结果在客户端可能缓存 6 小时。浏览器直连失败时，LibreTV 会尝试直播代理，但代理服务器所在地网络也会影响结果。本项目在本地解码通过不代表用户部署的 LibreTV 一定能播；失败时请记录实际错误提示、频道和 LibreTV 访问地址。

仓库刚创建时订阅可能只有 M3U 文件头。系统需要至少两轮检测才会发布首批频道。

## GitHub Actions

- `update.yml`：北京时间每天 03:23 自动检测和更新。
- `discover.yml`：北京时间每周日 02:41 搜索 GitHub 新来源。
- `pages.yml`：当网页或输出变化、或每日/每周检测任务成功完成时部署 GitHub Pages，并读取默认分支的最新输出。

在仓库的“设置 → Pages”中，将发布来源选择为 GitHub Actions。工作流使用仓库自带的 `GITHUB_TOKEN`，不需要自行创建个人访问令牌。首次推送后可在 Actions 页面手动运行“每周发现新播放源”，第二天再运行一次“每日检测并更新订阅”，用于完成连续两次检测门槛。

## 故障处理

- `ffprobe` 不存在：安装 FFmpeg，并确认 `ffprobe -version` 可执行。
- 没有生成频道：检查 `report.json`、频道别名、最低清晰度和连续成功次数。
- 任务绿色但频道仍为空：查看网页的“运行报告”。`discovered_sources` 为抓到的线路数，`matched_sources` 为白名单命中数；`filter_reasons` 中的 `WAITING_SECOND_SUCCESS` 表示等待第二轮成功，`BELOW_HD_THRESHOLD` 表示低于高清门槛。`issues` 会记录 GitHub 搜索或种子抓取失败，CLI 同时输出失败代码。
- 绿点但播放广告、无信号画面或错误频道：播放器测活和项目评分不能替代节目内容核验。报告中的 `PLACEHOLDER_STREAM` 表示占位源，`NON_LIVE_PLAYLIST` 表示非直播视频，`MANUAL_REJECTION` 表示人工禁用的问题线路。请反馈频道名称和实际画面，维护者应移除问题线路，不以提高频道数量为目标保留它们。
- GitHub 搜索被限流：等待下一次每周任务；系统会保留已有候选和订阅。
- Actions 无法提交：确认工作流权限允许“读取和写入”。
- Pages 显示数据加载失败：确认 Pages 来源为 GitHub Actions，且 `pages.yml` 已成功完成。
- 电视播放速度与网页不同：网页测速来自 GitHub 节点；请以电视所在地网络实测为准。

## 授权与来源说明

项目只处理无需登录且公开可访问的资源，不破解、不绕过认证、不生成受保护签名、不保存节目内容，也不提供视频中转或转码。公开可访问并不等于已获得再分发许可。

官方源和社区源分别发布并标注。提交新来源时必须提供发现地址；权利人或来源维护者要求移除时，请按 `SECURITY.md` 中的联系流程提交说明，维护者应及时禁用对应来源或 URL。

## 开发

```bash
python -m pytest -v
iptv-manager validate-output --output-dir output
```

详细设计和实施计划位于 `docs/superpowers/`。
