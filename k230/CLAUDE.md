# K230 人脸识别智能系统 — 项目规范与路线图

> ⚠️ **本目录是 K230 / CanMV 项目，不是 MaixPy4。**
> 上级目录 `D:\student_code\maixcam\CLAUDE.md` 的「只用 MaixPy v4」规范**不适用于本目录**。
> 本项目由 MaixPy4 版 `maix-dostudy` 移植到亚博智能 K230（CanMV MicroPython）。

---

## 一、平台与语法（强制）

- **芯片**：嘉楠 K230，双核 RISC-V；固件 CanMV MicroPython
- **必须**用 CanMV 语法，**禁止** MaixPy4 语法：

| 用途 | ✅ K230 CanMV | ❌ 禁止(MaixPy4) |
|------|--------------|-----------------|
| 摄像头 | `Sensor()` / `sensor.snapshot()` | `camera.Camera()` / `cam.read()` |
| 显示 | `Display.init(Display.ST7701,...)` / `Display.show_image()` | `display.Display()` / `disp.show()` |
| AI | `AIBase` + `nn.kpu` + `.kmodel` | `nn.YOLOv5(model=".mud")` |
| 退出 | `os.exitpoint()` | `app.need_exit()` |
| 导入 | `from media.sensor import *` | `from maix import camera` |

- 编码用 AI 推理走 `PipeLine`(libs/PipeLine.py) + `AIBase`(libs/AIBase.py) + `Ai2d`(libs/AI2D.py)
- 外设走 `ybUtils/`（YbRGB / YbKey / YbBuzzer / YbSpeaker / YbUart）

---

## 二、核心架构约束（务必牢记，这是一切取舍的根源）

K230 资源有限，**不能把所有功能同时跑**。三条硬约束：

1. **单 GIL**：所有 Python 线程**串行**抢一把锁（并发≠并行）。主循环+推流线程+音频线程会互相饿死。
2. **KPU 唯一**：人脸 KPU 与语音/其它 KPU 抢算力与内存池。
3. **媒体管线唯一**：本机录 MP4 要拆/重建 pipeline → 与人脸识别**天然互斥**。

### 设计总原则：**时分复用 + 职责外移**
1. K230 只做搬不走的事：摄像头采集、视觉 AI 推理、视频编码推流。
2. **存储外移**：录像推 RTSP 到电脑，电脑端 ffmpeg 存 mp4（电脑端软件用户自行开发，不在本仓库）。
3. **音频按需外移**：优先尝试 K230 在线语音；若卡顿/效果差，改外接串口语音模块，让 K230 完全不碰音频。
4. **兜底互斥**：任何重操作（录制等）若必须本机，照搬"暂停人脸识别 pipeline → 执行 → 恢复"模式，**绝不与人脸识别并发**。

### 已验证的安全并发组合
> **人脸识别(KPU) + RTSP 纯视频推流(VENC+网络)** = 流畅。
> 一旦再叠加**音频采集线程**就卡顿（GIL 抢占 + VENC 帧积压 pack_cnt 飙升）。
> 因此 **RTSP 默认不带音频**。

---

## 三、当前模块结构

| 文件 | 职责 | 状态 |
|------|------|------|
| `main.py` | 主应用/主循环/状态机装配/绘制 | ✅ |
| `config.py` | 全局配置（路径/阈值/WiFi/RTSP/语音关键词） | ✅ |
| `state_machine.py` | 轻量状态机（enter/exit/handler 回调） | ✅ |
| `face_detector.py` | 人脸检测+识别+录入（检测/识别两段KPU） | ✅ |
| `key_manager.py` | 按键（短按/长按/超长按） | ✅ |
| `led_controller.py` | LED 状态灯（YbRGB） | ✅ |
| `wifi_manager.py` | WiFi 连接 | ✅ |
| `rtsp_manager.py` | RTSP 推流（VENC 直推，复用主 sensor） | ✅ 视频；音频待回退 |
| `online_voice_recognition.py` | 在线语音识别（DashScope） | ⬜ 待移植 |
| `status_server.py` | HTTP 状态上传+命令下发 | ⬜ 待移植 |

### 状态机当前状态
- `RECOGNIZING(1)`：实时检测画框 + 节流完整识别拿 label
- `ENROLLING(2)`：仅检测画框，短按录入当前帧
- 长按：识别 ⇄ 录入 切换；超长按：退出；识别态短按：开/关 RTSP

---

## 四、按键交互约定（现有）

| 操作 | 识别态 | 录入态 |
|------|--------|--------|
| 短按 | 开/关 RTSP 推流 | 录入当前帧人脸 |
| 长按(≥1.5s) | → 录入态 | → 识别态 |
| 超长按(≥3s) | 退出 | 退出 |

---

## 五、路线图（分阶段，每步独立可验证）

> 任务清单见会话 TaskList。阶段 B（电脑端 ffmpeg 录制软件）由用户单独开发，**不在本仓库**。

### 阶段 A — RTSP 回退纯视频（基线）🔴 当前
把 `rtsp_manager.py` 的 `enable_audio` 改回 `False`，删音频采集线程，恢复流畅。
长跑验证「人脸识别 + 推流」稳定（观察 `pack_cnt` 不持续 >4）。**这是后续一切的稳定基线。**

### 阶段 C-1 — 在线语音识别（先试在线）
新建 `online_voice_recognition.py`，参考原项目笔记：
```
麦克风采PCM → WebSocket上传 → DashScope paraformer-realtime-v2 → 识别文本 → 中文关键词子串匹配 → 回调
```
要点：
- WebSocket 连 `wss://dashscope.aliyuncs.com/api-ws/v1/inference/`，发 `run-task`(model=paraformer-realtime-v2, format=pcm, 16kHz)
- 独立 `_audio_sender` 线程，100ms 音频块二进制上传
- `result-generated` 事件解析 `sentence.text` + `sentence_end`
- 整句结束做 `kw in text` 子串匹配，命中触发回调
- 去抖 1.5s（`TRIGGER_DEBOUNCE_MS`）；断线 800ms 重连
- **`pause_callback` 资源让渡**：语音激活时暂停人脸识别，让出 GIL/音频资源（时分复用）
- 关键词表见 `config.ONLINE_VOICE_KEYWORDS`；API Key 见 `config.DASHSCOPE_API_KEY`

> ⚠️ **核心矛盾**：在线语音=又一条音频采集线程，必然与人脸识别抢 GIL。
> 策略只能是「语音监听时降载/暂停视觉」。若实测卡顿无解或识别率差 → 转阶段 C-2。

### 阶段 C-2 — 串口语音模块（备选，C-1 不行才做）
K230 加 UART 线程，外接离线语音模块（如 SU-03T / ASRPRO）。
协议：模块→K230 命令码（录入/识别/推流/停止…）；K230→模块 播报码。
**好处**：K230 一行音频代码都不碰，根治卡顿。

### 阶段 D-1 — 远程录制
上位机下发录制命令时 K230 开始录制。
先实现独立录制（学长 `MP4Recorder` 的 pause/resume，与人脸识别互斥），再评估能否与识别并存。

### 阶段 D-2 — 状态上传 + 命令下发（HTTP）
移植 `status_server.py`：
- **上传**：主循环每 N 帧 `update_status()` 传人脸元数据；指纹变化检测 + 预缓存 JSON
- **下发**：POST `/command` 入队（`_cmd_queue`），主循环 `pop_command()` 在主线程执行（线程安全）
- 端口 **8001**（避开 JpegStreamer 8000）；支持 CORS
- 指令：`start/stop_recognize` / `start_enroll` / `enroll_face` / `rtsp` / `record` / `set_threshold` / `clear_faces` …

### 阶段 E — GUI 触摸界面
参考学长 `TouchUI`：ST7701 触摸屏侧边栏模式切换 + 动作按钮，替代纯按键。
模式：识别 / 录入 / 推流 / 录制 / 设置。保留物理按键作兜底。

---

## 六、关键路径与配置

```
模型:   /sdcard/kmodel/face_detection_320.kmodel   (320, anchor-based)
        /sdcard/kmodel/face_recognition.kmodel     (112, 特征提取)
锚框:   /sdcard/utils/prior_data_320.bin           (4200,4)
人脸库: /data/face_db/                              (每标签一个 .bin)
录像:   /data/recordings/
显示:   ST7701 LCD 640x480 (豪华版)
RTSP:   rtsp://<IP>:8554/test  H264 纯视频
```
阈值：检测 conf 0.5 / IoU 0.2；识别相似度 0.60（`dot/2+0.5`）。
识别节流：`RECOGNIZE_DETECT_INTERVAL_MS=800`（推流时若卡可调大）。

---

## 七、编码 / 协作规范

1. 改动遵循现有模块风格（中文注释、`print("[模块] ...")` 日志前缀）。
2. 新增重资源功能前，先问：**会和人脸识别并发吗？** 会 → 用 pause/resume 互斥。
3. 任何音频相关改动，警惕 GIL 抢占；优先线程让步（`time.sleep_ms`）+ 增大 chunk。
4. 主循环异常必须 try-except 包裹，单帧失败不能整机崩。
5. 资源清理在 `_cleanup`/`deinit`，逆序释放（编码器→link→server→media）。
6. 提交前自检：导入是否全 CanMV 语法、是否误用 MaixPy4 API。
