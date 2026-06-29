"""WebSocket 连接管理 + 广播。"""
from __future__ import annotations

import asyncio

from fastapi import WebSocket


class WsHub:
    """管理 /ws/detections 的连接，向所有客户端广播检测消息。"""

    def __init__(self) -> None:
        self._conns: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._conns.add(ws)

    async def remove(self, ws: WebSocket) -> None:
        async with self._lock:
            self._conns.discard(ws)

    async def broadcast(self, message: str) -> None:
        async with self._lock:
            conns = list(self._conns)
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._conns.discard(ws)
