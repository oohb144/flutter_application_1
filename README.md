# K230 联机电脑端（Flutter + Python 后端）

K230 视觉模块负责 RTSP 推流 + 音频播报 + HTTP 命令服务（:8001）；
本目录为**电脑端**：Flutter（Windows 桌面）拉流显示 + Python 后端做人脸识别 +
GUI（识别结果 / 人脸库 / 状态栏 / 命令按钮）+ 识别结果回传 K230 触发播报。

接口契约见 [`K230联机方案与接口.md`](./K230联机方案与接口.md)。

## 架构

```
K230 --RTSP--> media_kit 显示（Flutter）
K230 --RTSP--> Python 后端（InsightFace 检测+识别）
                 │
              WebSocket {bbox,label,known,score}
                 ▼
           Flutter 叠加画框
           POST /face_result --> K230 播报
           POST /command {rtsp/speak/led/exit} --> K230
```

## 目录

- `lib/` — Flutter 应用
  - `core/config.dart` — 端口/URL/阈值常量
  - `data/models/` — K230Status / K230Command / FaceResult / Detection
  - `data/services/` — K230 HTTP 客户端 / 后端 HTTP 客户端 / WS 客户端 / 后端进程管理 / face_result 节流
  - `data/providers/` — Settings / K230Status 轮询 / Detection / BackendProcess
  - `presentation/` — pages（home/face_db/settings）+ widgets（rtsp_player/face_overlay/status_bar/command_buttons/backend_control）
  - `utils/coord_mapper.dart` — 640×480 → 显示区坐标映射
- `backend/` — Python 后端（FastAPI + InsightFace）+ mock K230
  - 详见 [`backend/README.md`](./backend/README.md)

## 运行（Windows 桌面）

```bash
# 1. 装依赖
flutter pub get

# 2. 装 Python 后端依赖
cd backend && pip install -r requirements.txt && cd ..

# 3.（可选，无真机时）启 mock K230
python backend/mock_k230.py

# 4. 启 Flutter（GUI 里有「启动后端」按钮，或手动 python backend/main.py）
flutter run -d windows
```

设置页填入 K230 IP（屏显 IP），人脸库页录入熟人。

## Windows 构建已知问题（media_kit ANGLE/libmpv 解压）

`flutter build windows` 可能因 `media_kit_libs_windows_video` 用 `cmake -E tar xzf`
解压 `.7z` 归档失败（错误 `MSB3073`，ANGLE_EXTRACT）。原因：Visual Studio 自带
CMake 的 libarchive 不含 7z 支持。

修复（二选一）：

1. **装 7-Zip 后用预解压脚本**（推荐）：
   ```bash
   winget install 7zip.7zip
   flutter build windows --debug        # 先跑一次，让其下载 .7z（解压失败没关系）
   pwsh scripts/prepare_windows_build.ps1
   flutter build windows --debug        # 再跑，跳过解压步骤
   ```
2. **装官方 CMake**（cmake.org，libarchive 含 7z）并加入 PATH，让 flutter 优先使用它。

> 此问题仅影响 Windows 原生链接，不影响 Dart 代码（`flutter analyze` / `flutter test` 均通过）。

## 验证里程碑

- M1 推流长跑：media_kit 拉流 5 分钟不卡
- M2 HTTP：状态栏轮询 + 命令按钮（mock 或真机）
- M3 音频：speak 按钮（真机依赖 K230 端 TTS 集成）
- M4 face_result：识别到人 → POST /face_result（mock K230 打印）
- M5 联机：真机端到端跑通（需 K230 端先把 `http_cmd_server` 集成进 `main.py`）

> K230 端 `http_cmd_server.py` 已实现但未集成进 `main.py`，真实联调前需在 K230 端补集成（另开任务）。
