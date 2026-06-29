"""Mock K230 HTTP 命令服务（无真机时联调用）。

监听 :8001，实现与 K230 http_cmd_server.py 一致的最小接口：
  GET  /status       -> 固定 JSON
  POST /command      -> 打印 body，返回 {"ok":true,"msg":"queued"}
  POST /face_result  -> 打印 body，返回 {"ok":true,"msg":"queued"}
  OPTIONS *          -> 204 + CORS 头

运行：python mock_k230.py [--port 8001] [--ip 192.168.123.183]
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CORS_HEADERS = [
    ("Access-Control-Allow-Origin", "*"),
    ("Access-Control-Allow-Methods", "GET,POST,OPTIONS"),
    ("Access-Control-Allow-Headers", "Content-Type"),
]


class Handler(BaseHTTPRequestHandler):
    def _cors(self) -> None:
        for k, v in CORS_HEADERS:
            self.send_header(k, v)

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/status":
            self._json(200, {
                "ip": self.server.k230_ip,
                "rtsp_url": f"rtsp://{self.server.k230_ip}:8554/test",
                "rtsp_running": True,
                "state": "空闲",
                "audio_busy": False,
            })
        else:
            self._json(404, {"ok": False, "msg": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode()) if raw else {}
        except Exception:
            payload = {"_raw": raw.decode(errors="replace")}
        print(f"[mock K230] {self.path} <- {json.dumps(payload, ensure_ascii=True)}")
        if self.path in ("/command", "/face_result"):
            self._json(200, {"ok": True, "msg": "queued"})
        else:
            self._json(404, {"ok": False, "msg": "not found"})

    def log_message(self, fmt, *args):  # noqa: A003
        pass  # 静默默认日志，POST 已自行打印


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--ip", default="192.168.123.183", help="屏显用的 K230 IP")
    args = ap.parse_args()

    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    srv.k230_ip = args.ip  # type: ignore[attr-defined]
    print(f"[mock K230] listen :{args.port}  (status.ip={args.ip})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[mock K230] stop")


if __name__ == "__main__":
    main()
