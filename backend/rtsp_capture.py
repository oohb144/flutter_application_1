"""RTSP 拉流 + 人脸检测/识别 + WS 广播（后台线程）。

OpenCV 在独立线程拉流，每隔 [interval] 秒送一帧给 InsightFace，
识别结果经 asyncio 线程安全地广播到所有 WS 客户端。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

import cv2
import numpy as np

from face_db import FaceDB
from ws_hub import WsHub


class RtspCapture(threading.Thread):
    def __init__(
        self,
        rtsp_url: str,
        face_app: Any,
        face_db: FaceDB,
        ws_hub: WsHub,
        loop: asyncio.AbstractEventLoop,
        threshold: float,
        interval: float = 0.3,
    ) -> None:
        super().__init__(daemon=True)
        self._rtsp_url = rtsp_url
        self._face_app = face_app
        self._face_db = face_db
        self._ws_hub = ws_hub
        self._loop = loop
        self._threshold = threshold
        self._interval = interval
        self._stop = threading.Event()
        self._frame_lock = threading.Lock()
        self._last_frame: np.ndarray | None = None
        self._cap: cv2.VideoCapture | None = None

    def stop(self) -> None:
        self._stop.set()

    def get_last_frame(self) -> np.ndarray | None:
        with self._frame_lock:
            return None if self._last_frame is None else self._last_frame.copy()

    def run(self) -> None:
        retry = 0.0
        while not self._stop.is_set():
            if self._cap is None:
                self._cap = cv2.VideoCapture(self._rtsp_url)
                if not self._cap.isOpened():
                    print(f"[RtspCapture] open failed: {self._rtsp_url}")
                    time.sleep(min(2.0, 0.5 + retry))
                    retry += 0.5
                    self._cap = None
                    continue
                retry = 0.0
                print(f"[RtspCapture] opened: {self._rtsp_url}")

            ok, frame = self._cap.read()
            if not ok:
                print("[RtspCapture] read failed, reconnecting...")
                self._cap.release()
                self._cap = None
                time.sleep(1.0)
                continue

            with self._frame_lock:
                self._last_frame = frame

            try:
                boxes = self._detect_and_recognize(frame)
                msg = json.dumps({"boxes": boxes})
                asyncio.run_coroutine_threadsafe(
                    self._ws_hub.broadcast(msg), self._loop
                )
            except Exception as e:  # pragma: no cover
                print(f"[RtspCapture] process error: {e}")

            # 控频：避免 CPU 拉满
            time.sleep(self._interval)

        if self._cap is not None:
            self._cap.release()
        print("[RtspCapture] stopped")

    def _detect_and_recognize(self, frame: np.ndarray) -> list[list[Any]]:
        faces = self._face_app.get(frame)
        boxes: list[list[Any]] = []
        for face in faces:
            x1, y1, x2, y2 = [int(round(v)) for v in face.bbox[:4]]
            emb = face.embedding
            label, score = self._face_db.recognize(emb, self._threshold)
            known = label is not None
            boxes.append(
                [x1, y1, x2, y2, label if label else "unknown", known, round(float(score), 4)]
            )
        return boxes
