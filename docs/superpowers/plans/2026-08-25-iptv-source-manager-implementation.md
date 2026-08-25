# IPTV 播放源管理工具实施计划

> **供自动化开发代理使用：** 实施时必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，逐任务执行。所有步骤使用复选框跟踪。

**目标：** 构建一个能发现、检测、评分、清理并发布高速稳定高清 IPTV 播放源的 Python 工具，同时提供 GitHub Actions 自动更新和轻量 GitHub Pages 状态页。

**架构：** 使用文件化流水线和职责单一的 Python 模块。候选源经过解析、标准化、HTTP 初筛、`ffprobe` 复检、频道识别、历史评分和发布策略后，生成四份 M3U 订阅及网页 JSON；本地 CLI 与 GitHub Actions 共用同一套业务逻辑。

**技术栈：** Python 3.12、httpx、PyYAML、Pydantic 2、pytest、pytest-asyncio、FFmpeg/ffprobe、GitHub Actions、原生 HTML/CSS/JavaScript。

**设计说明：** `docs/superpowers/specs/2026-08-25-iptv-source-manager-design.md`

## 全局约束

- 项目文档、实施计划、README、配置注释和用户可见页面使用中文；代码标识符使用英文。
- 只处理无需登录且公开可访问的资源，不破解、不绕过认证、不生成受保护签名。
- 不保存媒体内容，不中转或转码视频。
- 第一版只发布 HTTP/HTTPS HLS、MPEG-TS 和 FLV。
- 默认最低清晰度为 720p；单频道可显式降级到 540p。
- 每个源只保留最近 30 次检测摘要。
- 输出校验失败时不得覆盖上一版有效订阅。
- 所有确定性单元测试不得依赖公网播放源。

---

## 文件职责图

- `pyproject.toml`：包元数据、运行依赖、测试和命令行入口。
- `src/iptv_manager/models.py`：配置、候选源、检测结果和发布结果的数据模型。
- `src/iptv_manager/config.py`：读取和校验三份 YAML 配置。
- `src/iptv_manager/parser.py`：解析 M3U 列表。
- `src/iptv_manager/normalize.py`：URL、频道名称和稳定 ID 标准化。
- `src/iptv_manager/discovery.py`：下载种子列表和调用 GitHub 搜索。
- `src/iptv_manager/probe_http.py`：异步 HTTP/HLS 初筛。
- `src/iptv_manager/probe_media.py`：受控调用 `ffprobe`。
- `src/iptv_manager/classify.py`：按白名单和别名识别频道。
- `src/iptv_manager/scoring.py`：滚动历史、评分、生命周期和主备选择。
- `src/iptv_manager/publisher.py`：生成并校验 M3U、状态 JSON 和报告 JSON。
- `src/iptv_manager/storage.py`：以原子方式读取和写入数据文件。
- `src/iptv_manager/pipeline.py`：编排完整流水线。
- `src/iptv_manager/cli.py`：本地和 CI 命令行入口。
- `config/*.yml`：种子、频道白名单和运行策略。
- `web/*`：无图片、无框架的静态状态页。
- `.github/workflows/*.yml`：每日检测、每周发现和 Pages 部署。

---

### 任务 1：建立可测试的 Python 项目骨架与配置模型

**文件：**
- 新建：`pyproject.toml`
- 新建：`src/iptv_manager/__init__.py`
- 新建：`src/iptv_manager/models.py`
- 新建：`src/iptv_manager/config.py`
- 新建：`config/sources.yml`
- 新建：`config/channels.yml`
- 新建：`config/policy.yml`
- 新建：`tests/test_config.py`

**接口：**
- 产出：`load_config(config_dir: Path) -> AppConfig`
- 产出：`StreamCandidate`、`CheckResult`、`CheckHistory`、`ChannelRule`、`PolicyConfig`、`AppConfig`

- [ ] **步骤 1：编写配置加载失败测试**

```python
def test_load_config_rejects_unknown_category(tmp_path: Path) -> None:
    write_minimal_config(tmp_path, category="电影")
    with pytest.raises(ValueError, match="频道分类"):
        load_config(tmp_path)
```

- [ ] **步骤 2：运行测试并确认因模块不存在而失败**

运行：`python -m pytest tests/test_config.py -v`

预期：测试收集失败，提示 `iptv_manager.config` 不存在。

- [ ] **步骤 3：建立包配置和严格数据模型**

`PolicyConfig` 必须包含 `http_timeout_seconds=5`、`media_probe_seconds=8`、`history_limit=30`、`minimum_height=720`、`switch_margin=8`、全局与单域名并发限制。分类字段只允许“央视、地方频道、香港、台湾”。时间统一使用带时区的 UTC `datetime`。

- [ ] **步骤 4：提供最小中文配置样例并实现 `load_config`**

配置加载顺序固定为 `sources.yml`、`channels.yml`、`policy.yml`；文件不存在、字段未知、阈值冲突时返回可操作的中文错误。

- [ ] **步骤 5：运行配置测试**

运行：`python -m pytest tests/test_config.py -v`

预期：全部通过。

- [ ] **步骤 6：提交任务 1**

```bash
git add pyproject.toml src/iptv_manager config tests/test_config.py
git commit -m "feat: 建立项目配置与数据模型"
```

### 任务 2：实现 M3U 解析、URL 标准化与去重

**文件：**
- 新建：`src/iptv_manager/parser.py`
- 新建：`src/iptv_manager/normalize.py`
- 新建：`tests/fixtures/sample.m3u`
- 新建：`tests/test_parser.py`
- 新建：`tests/test_normalize.py`

**接口：**
- 消费：`StreamCandidate`
- 产出：`parse_m3u(text: str, source_url: str, source_type: SourceType) -> list[StreamCandidate]`
- 产出：`normalize_url(url: str) -> str`
- 产出：`make_stream_id(url: str) -> str`
- 产出：`deduplicate_candidates(items: Iterable[StreamCandidate]) -> list[StreamCandidate]`

- [ ] **步骤 1：编写解析属性和相邻 URL 的失败测试**

```python
def test_parse_m3u_reads_name_group_and_url() -> None:
    text = '#EXTM3U\n#EXTINF:-1 tvg-id="cctv-1" group-title="央视",CCTV-1 综合\nhttps://a.example/live.m3u8\n'
    item = parse_m3u(text, "https://list.example/a.m3u", SourceType.COMMUNITY)[0]
    assert item.raw_name == "CCTV-1 综合"
    assert item.url == "https://a.example/live.m3u8"
```

- [ ] **步骤 2：运行解析和标准化测试并确认失败**

运行：`python -m pytest tests/test_parser.py tests/test_normalize.py -v`

- [ ] **步骤 3：实现有大小与行数限制的 M3U 解析器**

解析器支持 UTF-8 BOM、CRLF、逗号后的频道名和常用 `#EXTINF` 属性；忽略空行和注释；没有相邻 URL、协议不支持或单行过长时写入解析问题而非崩溃。

- [ ] **步骤 4：实现 URL 标准化和稳定去重**

仅接受 `http`/`https`；协议和主机名转小写，删除默认端口与片段，保留路径和有意义的查询参数；重复 URL 合并来源记录，不擅自删除可能影响播放的签名参数。

- [ ] **步骤 5：运行测试并确认通过**

运行：`python -m pytest tests/test_parser.py tests/test_normalize.py -v`

- [ ] **步骤 6：提交任务 2**

```bash
git add src/iptv_manager/parser.py src/iptv_manager/normalize.py tests
git commit -m "feat: 解析并标准化 M3U 播放源"
```

### 任务 3：实现受控的来源发现

**文件：**
- 新建：`src/iptv_manager/discovery.py`
- 新建：`tests/test_discovery.py`

**接口：**
- 消费：`AppConfig`、`parse_m3u`
- 产出：`fetch_seed_sources(config: AppConfig, client: httpx.AsyncClient) -> DiscoveryResult`
- 产出：`search_github(config: AppConfig, token: str, client: httpx.AsyncClient) -> DiscoveryResult`

- [ ] **步骤 1：编写响应大小限制和 GitHub 分页测试**

```python
@pytest.mark.asyncio
async def test_seed_larger_than_limit_is_rejected(mock_transport, app_config) -> None:
    result = await fetch_seed_sources(app_config, make_client(mock_transport))
    assert result.issues[0].code == "SOURCE_TOO_LARGE"
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`python -m pytest tests/test_discovery.py -v`

- [ ] **步骤 3：实现种子抓取**

应用连接、读取、响应大小、重定向和单域名并发限制；来源之间隔离失败；只把成功解析的候选源返回给流水线。

- [ ] **步骤 4：实现 GitHub API 搜索**

使用 `Authorization: Bearer` 但不得记录令牌；处理分页、速率限制和二次限流；只下载配置允许且大小合格的公开文本文件；发现结果仍进入候选池。

- [ ] **步骤 5：运行测试并提交**

运行：`python -m pytest tests/test_discovery.py -v`

```bash
git add src/iptv_manager/discovery.py tests/test_discovery.py
git commit -m "feat: 添加受控播放源发现"
```

### 任务 4：实现异步 HTTP 与 HLS 初筛

**文件：**
- 新建：`src/iptv_manager/probe_http.py`
- 新建：`tests/test_probe_http.py`

**接口：**
- 消费：`StreamCandidate`、`PolicyConfig`
- 产出：`probe_http(candidate: StreamCandidate, client: httpx.AsyncClient, policy: PolicyConfig) -> CheckResult`
- 产出：`probe_many_http(candidates: Sequence[StreamCandidate], policy: PolicyConfig) -> list[CheckResult]`

- [ ] **步骤 1：编写有效 HLS、HTML 错误页、超时和重定向循环测试**

```python
@pytest.mark.asyncio
async def test_html_login_page_is_not_a_stream(candidate, client, policy) -> None:
    result = await probe_http(candidate, client, policy)
    assert result.success is False
    assert result.failure_code == "HTML_RESPONSE"
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`python -m pytest tests/test_probe_http.py -v`

- [ ] **步骤 3：实现有限读取和协议识别**

记录 DNS/连接可见错误、首字节时间和有限样本吞吐；识别 HLS、MPEG-TS、FLV 和 HTML；所有派生 URL 再次验证协议与重定向限制。

- [ ] **步骤 4：实现 HLS 主列表、媒体列表和分片检查**

从主列表选择分辨率满足策略且带宽最高的可访问变体；媒体列表必须包含有效分片；只读取验证所需的有限字节。

- [ ] **步骤 5：实现全局与单域名并发限制**

用全局 `asyncio.Semaphore` 和按主机信号量限制请求；保持输入输出顺序稳定，便于确定性测试和报告。

- [ ] **步骤 6：运行测试并提交**

运行：`python -m pytest tests/test_probe_http.py -v`

```bash
git add src/iptv_manager/probe_http.py tests/test_probe_http.py
git commit -m "feat: 添加异步播放源初筛"
```

### 任务 5：实现受控 ffprobe 媒体复检

**文件：**
- 新建：`src/iptv_manager/probe_media.py`
- 新建：`tests/test_probe_media.py`

**接口：**
- 消费：HTTP 初筛成功的 `StreamCandidate` 与 `CheckResult`
- 产出：`build_ffprobe_args(url: str, policy: PolicyConfig) -> list[str]`
- 产出：`probe_media(candidate: StreamCandidate, policy: PolicyConfig, runner: ProcessRunner) -> CheckResult`

- [ ] **步骤 1：编写视频轨道、纯音频、超时和畸形 JSON 测试**

```python
@pytest.mark.asyncio
async def test_audio_only_stream_fails(candidate, fake_runner, policy) -> None:
    fake_runner.stdout = '{"streams":[{"codec_type":"audio"}]}'
    result = await probe_media(candidate, policy, fake_runner)
    assert result.failure_code == "NO_VIDEO_STREAM"
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`python -m pytest tests/test_probe_media.py -v`

- [ ] **步骤 3：实现固定参数列表和可替换进程执行器**

不得通过 shell 拼接命令；参数列表包含 JSON 输出、严格探测时限和网络超时；进程超时后终止并等待回收；标准输出与错误输出均限制大小。

- [ ] **步骤 4：解析视频、音频、清晰度、帧率和码率**

至少一个视频轨道才成功；无效 JSON、退出码异常、超时和缺少媒体轨道分别返回稳定失败代码。

- [ ] **步骤 5：运行测试并提交**

运行：`python -m pytest tests/test_probe_media.py -v`

```bash
git add src/iptv_manager/probe_media.py tests/test_probe_media.py
git commit -m "feat: 添加 ffprobe 媒体复检"
```

### 任务 6：实现频道白名单、别名与四类分类

**文件：**
- 新建：`src/iptv_manager/classify.py`
- 修改：`config/channels.yml`
- 新建：`tests/test_classify.py`

**接口：**
- 消费：`ChannelRule`、候选源原始名称
- 产出：`normalize_channel_name(name: str) -> str`
- 产出：`classify_channel(name: str, rules: Sequence[ChannelRule]) -> ChannelMatch`

- [ ] **步骤 1：编写央视别名、地方卫视、港澳台和歧义拒绝测试**

```python
@pytest.mark.parametrize("raw", ["CCTV1", "CCTV-1 综合", "央视一套"])
def test_cctv1_aliases_match(raw: str, rules) -> None:
    assert classify_channel(raw, rules).channel_id == "cctv-1"
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`python -m pytest tests/test_classify.py -v`

- [ ] **步骤 3：实现保守的名称标准化与精确别名匹配**

统一全半角、空白、大小写和常见分隔符；只有唯一匹配才返回频道；多个规则命中时返回 `AMBIGUOUS_CHANNEL`，无命中返回 `UNKNOWN_CHANNEL`。

- [ ] **步骤 4：填充第一版显式频道白名单**

覆盖央视常用频道、主流省级卫视、选定香港频道和台湾知名频道；市县频道不得进入地方频道配置。每项包含标准 ID、名称、分类和明确别名。

- [ ] **步骤 5：运行测试并提交**

运行：`python -m pytest tests/test_classify.py -v`

```bash
git add src/iptv_manager/classify.py config/channels.yml tests/test_classify.py
git commit -m "feat: 添加频道白名单与分类"
```

### 任务 7：实现历史、评分、主备切换与清理状态机

**文件：**
- 新建：`src/iptv_manager/scoring.py`
- 新建：`tests/test_scoring.py`
- 新建：`tests/test_lifecycle.py`

**接口：**
- 产出：`append_history(history: Sequence[CheckResult], result: CheckResult, limit: int = 30) -> tuple[CheckResult, ...]`
- 产出：`calculate_score(history: Sequence[CheckResult], policy: PolicyConfig) -> float`
- 产出：`update_lifecycle(stream: StreamCandidate, now: datetime, policy: PolicyConfig) -> StreamCandidate`
- 产出：`select_lines(streams: Sequence[StreamCandidate], policy: PolicyConfig) -> ChannelSelection`

- [ ] **步骤 1：编写 30 条截断、评分边界、两次失败和 30 天退休测试**

```python
def test_history_keeps_latest_thirty(results) -> None:
    history = ()
    for result in results[:31]:
        history = append_history(history, result)
    assert len(history) == 30
    assert history[0] == results[1]
```

- [ ] **步骤 2：编写 8 分切换滞后测试**

当前主源健康时，新线路需连续两轮高出至少 8 分才能替换；当前主源失败且备用源合格时立即替换。

- [ ] **步骤 3：运行测试并确认失败**

运行：`python -m pytest tests/test_scoring.py tests/test_lifecycle.py -v`

- [ ] **步骤 4：实现纯函数评分与状态转换**

可用率权重 40、速度 25、清晰度与码率 20、持续读取 15；所有分数限制在 0～100。状态转换只依赖输入历史、当前时间和策略，确保测试确定性。

- [ ] **步骤 5：实现每频道一个主源和最多两个备用源**

排序键首先比较是否达到硬门槛，再依次比较稳定性、启动速度、清晰度、码率和稳定 ID，确保相同输入产生相同输出。

- [ ] **步骤 6：运行测试并提交**

运行：`python -m pytest tests/test_scoring.py tests/test_lifecycle.py -v`

```bash
git add src/iptv_manager/scoring.py tests/test_scoring.py tests/test_lifecycle.py
git commit -m "feat: 添加质量评分与线路生命周期"
```

### 任务 8：实现原子存储与订阅发布

**文件：**
- 新建：`src/iptv_manager/storage.py`
- 新建：`src/iptv_manager/publisher.py`
- 新建：`tests/test_storage.py`
- 新建：`tests/test_publisher.py`

**接口：**
- 产出：`atomic_write_text(path: Path, content: str) -> None`
- 产出：`render_m3u(selections: Sequence[ChannelSelection], mode: PublishMode) -> str`
- 产出：`build_status(selections: Sequence[ChannelSelection], checked_at: datetime) -> dict[str, object]`
- 产出：`publish_outputs(result: PipelineResult, output_dir: Path) -> None`

- [ ] **步骤 1：编写无 Logo、中文分组和备用线路名称测试**

```python
def test_render_m3u_omits_logo(selection) -> None:
    text = render_m3u([selection], PublishMode.COMBINED)
    assert text.startswith("#EXTM3U\n")
    assert 'group-title="央视"' in text
    assert "tvg-logo" not in text
```

- [ ] **步骤 2：编写空输出不覆盖现有文件的测试**

先创建上一版有效文件，尝试发布空选择，断言抛出 `OutputValidationError` 且旧文件内容不变。

- [ ] **步骤 3：运行测试并确认失败**

运行：`python -m pytest tests/test_storage.py tests/test_publisher.py -v`

- [ ] **步骤 4：实现四种 M3U 与两个 JSON 输出**

所有 M3U 使用 UTF-8、LF 和标准 `#EXTINF`；输出顺序按分类配置、频道配置和线路优先级稳定排序。`status.json` 只包含页面所需字段，`report.json` 包含计数和稳定失败代码。

- [ ] **步骤 5：实现同目录临时文件加原子替换**

先完整生成并逐个校验所有输出，再进行替换；任一输出失败时删除临时文件并保留所有旧文件。

- [ ] **步骤 6：运行测试并提交**

运行：`python -m pytest tests/test_storage.py tests/test_publisher.py -v`

```bash
git add src/iptv_manager/storage.py src/iptv_manager/publisher.py tests
git commit -m "feat: 生成并安全发布 IPTV 订阅"
```

### 任务 9：编排完整流水线与命令行

**文件：**
- 新建：`src/iptv_manager/pipeline.py`
- 新建：`src/iptv_manager/cli.py`
- 新建：`tests/test_pipeline.py`
- 新建：`tests/test_cli.py`

**接口：**
- 产出：`run_pipeline(options: PipelineOptions, dependencies: PipelineDependencies) -> PipelineResult`
- 产出命令：`iptv-manager update`、`iptv-manager discover`、`iptv-manager validate-output`

- [ ] **步骤 1：编写确定性的候选到发布集成测试**

```python
@pytest.mark.asyncio
async def test_pipeline_promotes_only_twice_verified_hd_source(deps, options) -> None:
    result = await run_pipeline(options, deps)
    assert [item.channel_id for item in result.combined] == ["cctv-1"]
    assert result.combined[0].height == 1080
```

- [ ] **步骤 2：编写运行时间预算与部分失败测试**

达到预算后不再调度新候选，已完成结果仍可生成报告；如果没有可验证的新输出，则返回保留旧输出的明确结果。

- [ ] **步骤 3：运行测试并确认失败**

运行：`python -m pytest tests/test_pipeline.py tests/test_cli.py -v`

- [ ] **步骤 4：实现依赖注入式流水线**

按“加载配置、发现、解析去重、识别、HTTP 初筛、媒体复检、更新历史、评分选择、发布”执行。时钟、HTTP 客户端、媒体执行器和存储均可替换，确保测试不访问公网。

- [ ] **步骤 5：实现中文 CLI 帮助与退出码**

配置错误、输出校验错误、完全失败和部分成功使用明确退出码；日志默认隐藏敏感查询参数。

- [ ] **步骤 6：运行全套测试并提交**

运行：`python -m pytest -v`

```bash
git add src/iptv_manager/pipeline.py src/iptv_manager/cli.py tests
git commit -m "feat: 编排 IPTV 管理流水线"
```

### 任务 10：添加轻量 GitHub Pages 状态页

**文件：**
- 新建：`web/index.html`
- 新建：`web/styles.css`
- 新建：`web/app.js`
- 新建：`tests/test_web_assets.py`

**接口：**
- 消费：`output/status.json`
- 产出：央视、地方频道、香港、台湾筛选和四种订阅入口。

- [ ] **步骤 1：编写静态页面约束测试**

```python
def test_status_page_has_no_images_or_external_frameworks() -> None:
    html = Path("web/index.html").read_text("utf-8")
    assert "<img" not in html.lower()
    assert "https://cdn" not in html.lower()
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`python -m pytest tests/test_web_assets.py -v`

- [ ] **步骤 3：实现无图片、无框架的中文页面**

使用语义 HTML、系统字体、CSS 网格和少量原生 JavaScript。通过 `textContent` 渲染外部字段；提供分类、状态和来源类型筛选；表格展示分辨率、延迟、可用率、评分及检测时间。

- [ ] **步骤 4：加入加载失败与空数据状态**

`status.json` 加载失败时显示中文故障说明和直接订阅链接；移动端采用卡片或横向滚动，不加载图片或第三方脚本。

- [ ] **步骤 5：运行测试并提交**

运行：`python -m pytest tests/test_web_assets.py -v`

```bash
git add web tests/test_web_assets.py
git commit -m "feat: 添加轻量频道状态页"
```

### 任务 11：添加 GitHub Actions 自动检测、发现和 Pages 部署

**文件：**
- 新建：`.github/workflows/update.yml`
- 新建：`.github/workflows/discover.yml`
- 新建：`.github/workflows/pages.yml`
- 新建：`tests/test_workflows.py`

**接口：**
- 消费：CLI 三个命令。
- 产出：每日更新、每周发现、手动运行、自动提交和 Pages 部署。

- [ ] **步骤 1：编写工作流静态测试**

```python
def test_update_workflow_has_concurrency_and_manual_trigger() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/update.yml").read_text("utf-8"))
    assert "concurrency" in workflow
    assert "workflow_dispatch" in workflow["on"]
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`python -m pytest tests/test_workflows.py -v`

- [ ] **步骤 3：实现每日更新工作流**

安装 Python 3.12、项目依赖和 FFmpeg；先运行测试，再执行更新和输出校验；只暂存允许自动更新的 `data/` 与 `output/` 文件；仅有差异时提交。使用最小 `contents: write` 权限和并发锁。

- [ ] **步骤 4：实现每周发现与 Pages 工作流**

发现工作流使用 `GITHUB_TOKEN` 和只读 API 权限执行 `discover`；Pages 工作流把 `web/` 与 `output/status.json`、M3U 文件组装为部署制品，不执行第三方页面脚本。

- [ ] **步骤 5：运行测试并提交**

运行：`python -m pytest tests/test_workflows.py -v`

```bash
git add .github/workflows tests/test_workflows.py
git commit -m "ci: 添加自动检测发现与页面部署"
```

### 任务 12：完成中文文档、示例输出与端到端验证

**文件：**
- 新建：`README.md`
- 新建：`CONTRIBUTING.md`
- 新建：`SECURITY.md`
- 新建：`output/official.m3u`
- 新建：`output/community.m3u`
- 新建：`output/combined.m3u`
- 新建：`output/backup.m3u`
- 新建：`output/status.json`
- 新建：`output/report.json`
- 修改：`.gitignore`

**接口：**
- 产出：本地安装、FFmpeg 安装、配置、运行、GitHub 部署、订阅使用、维护和移除流程。

- [ ] **步骤 1：编写 README 内容检查测试**

检查 README 必须包含“本地运行”“GitHub Actions”“订阅地址”“检测指标”“授权与来源说明”“故障处理”六节，并说明 GitHub 节点测速不代表用户所在地体验。

- [ ] **步骤 2：编写全部中文文档**

README 给出 Windows、macOS、Linux 的 Python/FFmpeg 安装方式、CLI 示例、配置字段和 Pages 设置步骤。贡献指南要求新增频道必须提供标准名称、分类、别名和来源说明。安全文档提供私密报告和权利人移除流程。

- [ ] **步骤 3：生成可通过校验的空仓库初始输出**

初始 M3U 只含 `#EXTM3U`；状态与报告 JSON 使用带版本号的有效空结构。运行时发布规则仍禁止用意外空结果覆盖已有有效频道数据。

- [ ] **步骤 4：运行格式、测试和本地构建验证**

运行：

```bash
python -m pytest -v
python -m iptv_manager.cli validate-output --output-dir output
python -m iptv_manager.cli update --config-dir config --dry-run
```

预期：测试全部通过；初始输出结构有效；无公网或无可用种子时，试运行生成中文报告且不覆盖输出。

- [ ] **步骤 5：检查敏感信息和媒体文件**

运行：`git grep -nE "(Authorization:|Bearer [A-Za-z0-9]|Cookie:)" -- . ':!docs/superpowers/plans/*'`

预期：无硬编码凭据。再确认仓库不存在 `.ts`、`.mp4`、`.flv` 等媒体内容样本。

- [ ] **步骤 6：提交文档与初始发布文件**

```bash
git add README.md CONTRIBUTING.md SECURITY.md .gitignore output
git commit -m "docs: 完成部署使用与维护说明"
```

### 任务 13：最终验收

**文件：**
- 检查：全部项目文件

- [ ] **步骤 1：执行完整测试**

运行：`python -m pytest -v`

预期：全部通过，无跳过的核心业务测试。

- [ ] **步骤 2：检查 Git 状态与提交历史**

运行：`git status --short`，预期无未提交文件。运行 `git log --oneline --decorate -12`，确认每个任务都有独立提交。

- [ ] **步骤 3：人工复核关键产品约束**

确认页面无图片和第三方框架；订阅无 `tvg-logo`；分类只有四种；地方频道仅为省级卫视；默认订阅每频道只有一个主源；失败运行不能覆盖旧输出；日志不泄漏令牌和敏感查询参数。

- [ ] **步骤 4：在 GitHub 仓库手动运行工作流**

先运行更新工作流，再运行发现工作流，最后检查 Pages。确认自动提交只修改允许的生成文件，订阅 URL 可下载，页面能显示最新检测时间。

