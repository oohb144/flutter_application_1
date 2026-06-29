# K230 联机电脑端后端

FastAPI + InsightFace：独立拉 RTSP 流做人脸检测/识别，WebSocket 把检测框推给 Flutter；
并管理人脸特征库。不参与 Flutter 端 GUI。

## 安装

```bash
cd backend
pip install -r requirements.txt
```

> InsightFace 首次运行会自动下载 `buffalo_l` 模型到 `~/.insightface/models/`。
> 有 NVIDIA GPU：把 `requirements.txt` 的 `onnxruntime` 换成 `onnxruntime-gpu`，
> 启动加 `--providers CUDAExecutionProvider --ctx 0`。

## 启动

```bash
# 联机（真实 K230 推流）
python main.py --rtsp rtsp://192.168.123.183:8554/test --threshold 0.35

# 无 K230 时用本地视频文件联调
python main.py --rtsp sample.mp4

# 仅人脸库管理（不拉流）
python main.py
```

常用参数：`--host` `--port`(默认8000) `--rtsp` `--threshold` `--model` `--providers`
`--ctx`(-1=CPU) `--det-size` `--interval`(检测间隔秒)。

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 后端健康 + 采集状态 + 人脸数 |
| GET | `/face/list` | 人脸库列表 `[{name,count}]` |
| POST | `/face/register_from_rtsp` | 从当前流抓一帧录入 `name`（表单） |
| POST | `/face/register` | 上传图片录入（multipart: `name` + `file`） |
| DELETE | `/face/{name}` | 删除某人 |
| GET/POST | `/face/threshold` | 读取/设置相似度阈值 |
| WS | `/ws/detections` | 推送 `{boxes:[[x1,y1,x2,y2,label,known,score],...]}` |

bbox 坐标基于 RTSP 原始 640×480，Flutter 端按显示区缩放映射。

## Mock K230（无真机联调）

```bash
python mock_k230.py --port 8001 --ip 192.168.123.183
```

模拟 K230 的 `GET /status` / `POST /command` / `POST /face_result`，
收到命令在终端打印。用于验证 Flutter 的状态轮询、命令下发、face_result 回传。

## 文件

- `main.py` — FastAPI 入口 + 路由 + lifespan 启停采集线程
- `face_db.py` — 人脸特征库（numpy + 余弦比对，落盘 `face_db.npz`）
- `rtsp_capture.py` — OpenCV 拉流 + InsightFace 检测识别 + WS 广播（后台线程）
- `ws_hub.py` — WebSocket 连接管理 + 广播
- `mock_k230.py` — 模拟 K230 HTTP 接口
