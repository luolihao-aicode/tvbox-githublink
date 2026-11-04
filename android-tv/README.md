# Android TV 智选直播

原生 Kotlin / Views / Jetpack Media3 播放器，最低 Android 6.0（API 23），仅声明电视启动入口。GitHub 分发静态目录，应用直连上游，不转发视频，不接入 NewTV 私有协议。1.0.1 提供用户指定的央视与四个卫视个人试播目录，公开可访问不代表已确认节目分发授权；正式授权目录仍独立维护。

## 构建与安装

使用 JDK 17、Android SDK 36、Gradle 9.4.1 和 AGP 9.2.1（内置 Kotlin），Media3 固定 1.11.1。版本依据：[AGP 9.2 官方说明](https://developer.android.com/build/releases/agp-9-2-0-release-notes)、[Media3 官方稳定版](https://developer.android.com/jetpack/androidx/releases/media3)。首次构建需要下载依赖。

在本目录创建未纳入版本控制的 `local.properties`，填写 `sdk.dir`，或设置 `ANDROID_HOME`。Windows 执行：

```powershell
.\gradlew.bat testDebugUnitTest assembleDebug lintDebug
adb install -r app\build\outputs\apk\debug\app-debug.apk
adb shell am start -n com.luolihao.smarttv/.MainActivity
```

APK：`app/build/outputs/apk/debug/app-debug.apk`。此为标准调试签名，可安装测试；未配置正式发布签名，不代表已上架。

## 遥控器操作

- 方向键移动菜单焦点，OK 确认；全屏播放时 OK 打开频道列表。
- 返回键先关闭菜单，再退出应用；全屏上下键切台。
- 左侧第一列是央视、地方台等分类，第二列是当前分类的频道，右侧保留播放画面。左右键跨列，上下选台，OK 播放并收起菜单。
- 客户界面仅保留分类和频道，不显示设置、搜索、诊断、收藏、最近观看等入口。
- 目录、更新、稳定缓冲、选源与恢复内置运行。诊断、日志及自制测试媒体保留在内部实现和自动化测试中，客户无需配置。

新安装默认载入 `https://luolihao-aicode.github.io/tvbox-githublink/tv-test.json`。APK 内含构建时的已校验试播快照，目录网站暂时不可达时可先显示频道；视频仍需电视直连上游。旧版默认空目录自动迁移到试播目录，其他自定义地址保留。正式授权目录 `channels.json` 的授权控制保持独立。

## 调度与故障恢复

单个活跃 ExoPlayer，释放后才重建；低资源模式默认关闭备用 HTTP 预检。开启后也只对一个 HTTPS 备源做有时限的 HEAD 请求，不创建第二个播放器，不占用第二套解码器，不将 HTTP 成功认作媒体成功。

候选排序优先使用本机当前网络的真实成功、首帧耗时、卡顿与失败记录，服务端评分为次级依据。网络标识由传输类型和本地网关的短哈希组成，不记录网关明文或 Wi-Fi 名称。配置中同一源 ID 必须唯一。

独立健康监测器检查准备超时、首帧超时、持续缓冲、渲染帧计数、播放位置推进；视频无首帧即使 READY 也不会视为成功。音轨存在而没有视频轨的电视源会有界切备。静态画面仍有正常帧输出时不会因画面内容不变误判。暂停不触发冻结恢复。

每源最多重试一次、重建一次，然后隔离并切到尚未尝试的备源。解码或格式错误直接隔离切备；有冷却及递增退避。全部失败后停止自动尝试，用户换台、重新选择频道或网络恢复后建立新会话。重试预算不会在每次 READY 时重置，从而避免无限黑屏循环。阈值集中在 `PolicyConfig`。

已出现首帧后，60 秒内三次进入缓冲会以 `frequent_buffering` 原因直接切备，处理每隔几秒卡一下却未达到连续超时的情况；一秒观察窗口可能无法捕获更短的缓冲。此策略不通过降低分辨率实现换源。播放器的多码率自动选择仍受上游清单影响，不能把低清画面变成真实高清。

稳定优先配置的 Media3 目标缓冲范围为 20–45 秒，内存目标 24MB；均衡配置为 10–25 秒。故障后重建会采用稳定优先。参数是播放器缓冲目标，实际数据量受直播窗口、码率和网络限制，不能保证固定延迟或无缝拼接。

## 配置与缓存

接受 schemaVersion=1、minClientVersion≤1，拒绝未来不兼容版本。校验频道与源 ID、HTTP(S) 地址、协议、分数范围和最大 5MB 文档。`contentHash` 校验完整原始 `channels` 数组的 UTF-8 递归按键排序紧凑 JSON SHA-256，保留数组顺序和未知扩展字段；与服务端共享同一跨语言 fixture。

目录下载仅 HTTPS，重定向也要求 HTTPS，设置连接与读取超时以及大小限制。ETag 条件请求减少重复下载；新配置校验通过才写入临时文件并同步提交，保存上一份有效配置。网络失败、JSON 损坏或未来版本回退缓存；当前缓存损坏会尝试前一份。不同配置 URL 使用独立缓存，避免新地址失败时混入旧地址频道。

当前为未上架测试版，没有真实商店 ID 或更新渠道。后续接入商店时仅在设置页打开时查询一次版本，不在每次启动时查询；没有“旧版本已无法使用”的证据时不做启动强制升级。

## 本地数据与诊断文件

收藏、最近 50 个频道、每网络每源观看统计均仅保存在本机，不上传。观看时长为周期抽样估计，可能包含短时缓冲或遗漏区间，不作为精确计时；选源主要依据成功、失败、卡顿和首帧。质量记录最多保留 2000 项。日志自动写入：

```text
/sdcard/Android/data/com.luolihao.smarttv/files/Documents/diagnostics/
```

保留滚动日志和最近五份导出文件。URL 移除用户凭据、完整路径、查询参数和片段，只保留协议与主机；播放错误记录 Media3 错误码，不写入底层可能带令牌的异常堆栈。诊断按钮生成可直接交给开发人员的 `.txt` 文件。

```powershell
adb pull /sdcard/Android/data/com.luolihao.smarttv/files/Documents/diagnostics ./diagnostics
```

## 自动化验证

```powershell
.\gradlew.bat testDebugUnitTest assembleDebug lintDebug
# 连接 Android TV 或电视模拟器后：
.\gradlew.bat connectedDebugAndroidTest
```

JVM 测试使用可控时间戳验证无首帧、持续缓冲、冻结/暂停边界、有限重试、冷却隔离、本网络选源、配置损坏回退、版本拒绝、脱敏和服务端共享 hash。仪器测试在设备真实解码自制 H.264/AAC 媒体，检查首帧与播放进度、坏媒体切到好备源、全部失败终止、快速切台仅一个实例、遥控器操作和日志文件。

自制 `diagnostic.mp4` 为 FFmpeg testsrc2 彩条与 440Hz 测试音，无第三方版权节目。`invalid.bin` 专门模拟媒体格式错误，`audio-only.m4a` 专门模拟只有音轨的电视源；均不会出现在默认直播目录。

测试边界：模拟器验证不能证明实体电视硬件兼容性或真实宽带下的长时直播表现；未做普通 M3U 播放器、NewTV 的同源同设备体验比较，不宣称达到其效果。Media3 使用硬件解码回退能力，但不保证所有电视支持所有视频编码。不支持 DRM/会员破解、鉴权绕过、P2P、专有 UDP 或未经评估的其他媒体协议。Logos 暂只保留目录字段，不后台批量加载图片，以减少低端盒子的内存与网络压力。
