# Android TV 智能直播实施计划

> 执行方式：用户已要求检查后直接开发；按独立服务端与 Android 子任务并行实施，主代理集成与验证，不等待设计批准。

**目标：** 升级现有目录发布，并构建可安装的电视 APK。

**架构：** Python 沿用采集检测与评分，新增授权门控、多源 JSON、版本化发布；Kotlin TV 应用直连上游，Media3 播放，客户端自行监测与恢复。

**设计依据：** `docs/AndroidTV实施方案-2026-11-04.md` 及用户提交的完整任务书。

## 全局约束

- 文档及用户界面中文；不复制 NewTV 私有代码，不做 DRM/鉴权绕过。
- 无服务器/CDN/数据库运营成本，无观看数据上传；维持现有 M3U 接口。
- 发布指标必须可追溯，源必须允许应用使用，日志不含口令。
- 稳定性优先，单主播放器，备用资源有界；没有物理设备就不声称物理验收。

## 任务与验证

- [x] 服务端：授权表、catalog 模块、Schema、publisher/Actions 集成；先测试授权缺失/敏感 URL/多源/坏配置回退，再运行完整 pytest。
- [x] 电视应用：android-tv Gradle/manifest/UI/配置存储；验证坏 JSON、未来版本、ETag 304、空目录、重复 ID 与无网络启动。
- [x] 播放调度：监测、隔离/退避、快速切台会话、首帧及有声无画面、网络切换；用受控事件测试状态转换，不以一个直播 URL成功代替。
- [x] 本地诊断：统计、文件导出、脱敏、自制媒体；验证日志无查询参数和访问口令。
- [x] 构建安装：单元测试、lint、Debug APK、模拟器安装与TV交互；提供路径及ADB命令，记录真实设备和性能对照的未完成项。

生产授权频道、实体电视长时播放及 NewTV 同源对照尚未完成；详见 `docs/AndroidTV验收记录-2026-11-04.md`。

## 集成接口

客户端读取 `schemaVersion=1/minClientVersion=1/generatedAt/contentHash/channels[]`；频道包含 `id/name/group/logo/sources[]`，来源包含 `id/url/type/score/resolution/videoCodec/lastChecked/originGroup`。可选指标为空；`type` 限 hls、dash、progressive。摘要为规范化 channels 数组 SHA256；客户端与服务端共同固定数值序列化测试向量。

## 重点回归

旧目录正常但下载新目录损坏时必须继续使用缓存；快速重复切台不能让旧回调切走新频道；READY但没有首帧必须可恢复；应用退后台不得继续探测与持有多解码器；全部源失败不得无穷重试。
