"""K230 联机电脑端 Python 后端。

FastAPI + InsightFace：独立拉 RTSP 流做人脸检测/识别，
WebSocket 把检测框推给 Flutter 叠加显示；并管理人脸特征库。

启动示例：
    python main.py --rtsp rtsp://192.168.123.183:8554/test --threshold 0.35
    python main.py --rtsp sample.mp4                 # 本地视频文件（无 K230 时联调）
    python main.py                                   # 不拉流，仅人脸库管理
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from insightface.app import FaceAnalysis
import uvicorn

from face_db import FaceDB
from rtsp_capture import RtspCapture
from ws_hub import WsHub

# ---- 全局对象（lifespan 中初始化） ----
face_app: FaceAnalysis | None = None
face_db: FaceDB | None = None
ws_hub = WsHub()
capture: RtspCapture | None = None
threshold_state: float = 0.35


@asynccontextmanager
async def lifespan(app: FastAPI):
    global face_app, face_db, capture
    args = app.state.args
    face_db = FaceDB()

    if args.no_model:
        print("[main] --no-model: skipping FaceAnalysis init, face-db endpoints will fail")
    else:
        face_app = FaceAnalysis(
            name=args.model,
            allowed_modules=["detection", "recognition"],
            providers=[args.providers],
        )
        face_app.prepare(ctx_id=args.ctx, det_size=tuple(args.det_size))
        print(f"[main] FaceAnalysis ready: {args.model} providers={args.providers}")

    if args.rtsp and face_app is not None:
        loop = asyncio.get_running_loop()
        capture = RtspCapture(
            rtsp_url=args.rtsp,
            face_app=face_app,
            face_db=face_db,
            ws_hub=ws_hub,
            loop=loop,
            threshold=threshold_state,
            interval=args.interval,
        )
        capture.start()
        print(f"[main] RtspCapture started: {args.rtsp}")
    else:
        print("[main] no face detection running")

    yield

    if capture is not None:
        capture.stop()
    print("[main] shutdown")


def create_app(args: argparse.Namespace) -> FastAPI:
    global threshold_state
    threshold_state = args.threshold

    app = FastAPI(title="K230 联机后端", lifespan=lifespan)
    app.state.args = args

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict:
        return {
            "ok": True,
            "rtsp": bool(args.rtsp),
            "capture_running": capture is not None and capture.is_alive(),
            "face_count": sum(e["count"] for e in (face_db.list_entries() if face_db else [])),
        }

    # ---- 人脸库管理 ----
    @app.get("/face/list")
    async def face_list() -> dict:
        return {"entries": face_db.list_entries()}

    @app.get("/face/threshold")
    async def get_threshold() -> dict:
        return {"value": threshold_state}

    @app.post("/face/threshold")
    async def set_threshold(value: float = Form(...)) -> dict:
        global threshold_state
        threshold_state = value
        if capture is not None:
            capture._threshold = value  # noqa: SLF001
        return {"value": threshold_state}

    @app.delete("/face/{name}")
    async def face_delete(name: str) -> dict:
        ok = face_db.delete(name)
        return {"ok": ok}

    @app.post("/face/register_from_rtsp")
    async def register_from_rtsp(name: str = Form(...)) -> dict:
        if face_app is None:
            return {"ok": False, "msg": "FaceAnalysis not loaded (--no-model)"}
        if capture is None:
            return {"ok": False, "msg": "capture not running (no --rtsp)"}
        frame = capture.get_last_frame()
        if frame is None:
            return {"ok": False, "msg": "no frame yet"}
        faces = face_app.get(frame)
        if not faces:
            return {"ok": False, "msg": "no face detected"}
        n = face_db.register(name, faces[0].embedding)
        return {"ok": True, "name": name, "count": n}

    @app.post("/face/register")
    async def register_upload(name: str = Form(...), file: UploadFile = File(...)) -> dict:
        if face_app is None:
            return {"ok": False, "msg": "FaceAnalysis not loaded (--no-model)"}
        data = await file.read()
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return {"ok": False, "msg": "bad image"}
        faces = face_app.get(img)
        if not faces:
            return {"ok": False, "msg": "no face detected"}
        n = face_db.register(name, faces[0].embedding)
        return {"ok": True, "name": name, "count": n}

    # ---- WebSocket 推送检测框 ----
    @app.websocket("/ws/detections")
    async def ws_detections(ws: WebSocket) -> None:
        await ws_hub.add(ws)
        try:
            while True:
                await ws.receive_text()  # 客户端可发心跳，此处忽略
        except WebSocketDisconnect:
            pass
        finally:
            await ws_hub.remove(ws)

    return app


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="K230 联机后端")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--rtsp", default="", help="RTSP URL 或本地视频文件路径；留空则不拉流")
    p.add_argument("--threshold", type=float, default=0.35)
    p.add_argument("--model", default="buffalo_l", help="insightface 模型包名")
    p.add_argument(
        "--providers",
        default="CPUExecutionProvider",
        help="onnxruntime providers，逗号分隔；GPU 用 CUDAExecutionProvider",
    )
    p.add_argument("--ctx", type=int, default=-1, help="insightface ctx_id，-1=CPU，0=GPU0")
    p.add_argument("--det-size", type=int, nargs=2, default=[640, 640])
    p.add_argument("--interval", type=float, default=0.3, help="检测间隔秒数")
    p.add_argument("--no-model", action="store_true", help="跳过 InsightFace 模型加载（轻量启动，仅 WS/健康检查）")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    uvicorn.run(create_app(args), host=args.host, port=args.port)
