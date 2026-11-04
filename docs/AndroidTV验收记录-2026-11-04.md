# Android TV 测试版验收记录

日期：2026-11-04。代码和可安装测试版已交付；生产直播体验验收尚未完成，不宣称达到 NewTV 水平。

## 已交付

复用原 Python 流水线，新增授权准入、多源 JSON、区域受限历史候选、时效评分、来源分组、帧率和视频包码率、最近至多三个 HLS 分片抽样。保留旧订阅；新增 `channels.json`、`all.m3u`、Schema、版本目录和原子发布清单。每日/每周 Actions 与 Pages 接入，新建 Android 构建工作流。

Android 客户端提供原生 Kotlin/Media3、遥控器 UI、分类/搜索/收藏/最近、本网络选源、首帧/缓冲健康监测、有界恢复/切备、本地缓存和诊断。备用机制为默认关闭的单个 HTTP 预检，不保持第二套媒体预缓冲，不假装无缝拼接。

## 实际验证

| 检查 | 结果 |
| --- | --- |
| Python 全量回归 | 146 项通过 |
| validate-catalog / validate-output | 均通过 |
| 跨端摘要 | 同一中文/emoji、浮点、null 非空样本校验通过 |
| Android JVM | 20 项通过 |
| Android TV 模拟器 | 独立 API 33 SmartIPTV_QA，8 项通过，无跳过 |
| 构建及 lint | assembleDebug 成功；lint 为 0 错误、10 条警告，未屏蔽规则 |
| 最终 APK | 主代理再次 ADB 安装返回 Success，并确认启动与包路径 |
| 签名 | apksigner 验证通过，标准 Debug 签名，v1/v2，一个签名者 |

模拟器使用自制视频、音频单轨和坏媒体，检查首帧/进度、自动切备、全失败停止、快速切台单实例、遥控器、后台网络回调不复活播放及日志落盘。另保留 51 来源解析、81→1 频道分页收敛、API 23 下载兼容等回归。

## 安装包与日志

本机文件 `artifacts/SmartLiveTV-1.0.0-debug.apk`，6,616,131 字节，目录未纳入 Git；GitHub 构建包通过 Actions 制品提供。

SHA256：`4141EDF4C92CBBFF1EBC84414657BCD1F82D3ED569C7A9DFE4D41B54BE204480`

```powershell
adb install -r artifacts/SmartLiveTV-1.0.0-debug.apk
adb shell am start -n com.luolihao.smarttv/.MainActivity
adb pull /sdcard/Android/data/com.luolihao.smarttv/files/Documents/diagnostics ./电视诊断日志
```

打开应用“诊断”可运行自制媒体测试。日志自动保存于上述 Documents/diagnostics 目录，仅在本地保留，不上传观看历史。

## 未完成的最终验收与限制

- **生产频道授权缺失：** `usage_rights.yml` 没有可核验许可，新目录目前 0 频道。旧社区源和合成样本不能冒充已授权频道，需登记允许本应用使用及公开地址的真实许可。
- **没有实体电视连接：** 模拟器不证明真实电视的硬解码、遥控器、低内存或宽带长时间稳定性；未做 NewTV/第三方播放器同源同设备对比。
- **没有正式签名和商店渠道：** 仅交 Debug APK，不伪造正式签名、上架状态或商店更新地址。
- **首版边界：** 支持 HLS/DASH/可支持的 Progressive，不做专有 UDP/P2P/DRM/鉴权绕过。尚无完整 DASH 分片探测；历史仍保留最近 30 次检测，非严格 30 天；观看时长为抽样估计。
- **备用源范围：** 当前仅 HTTP 预检，不是完整媒体预缓冲。更深入的备用媒体和设备分级优化需在真实设备上评估收益与资源开销。
