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

## 检测指标

综合评分为 100 分：

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
```

- `combined.m3u`：普通用户推荐，每频道一个最佳线路，合格时优先官方源。
- `official.m3u`：仅官方来源。
- `community.m3u`：检测合格的社区发现来源。
- `backup.m3u`：主源及最多两个备用源。

仓库刚创建时订阅可能只有 M3U 文件头。系统需要至少两轮检测才会发布首批频道。

## GitHub Actions

- `update.yml`：北京时间每天 03:23 自动检测和更新。
- `discover.yml`：北京时间每周日 02:41 搜索 GitHub 新来源。
- `pages.yml`：当网页或输出变化时部署 GitHub Pages。

在仓库的“设置 → Pages”中，将发布来源选择为 GitHub Actions。工作流使用仓库自带的 `GITHUB_TOKEN`，不需要自行创建个人访问令牌。首次推送后可在 Actions 页面手动运行“每周发现新播放源”，第二天再运行一次“每日检测并更新订阅”，用于完成连续两次检测门槛。

## 故障处理

- `ffprobe` 不存在：安装 FFmpeg，并确认 `ffprobe -version` 可执行。
- 没有生成频道：检查 `report.json`、频道别名、最低清晰度和连续成功次数。
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
