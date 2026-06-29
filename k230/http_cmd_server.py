"""
亚博智能 K230 学长方案 - HTTP 命令服务器（CanMV）

socket 手写 HTTP server，端口 8001，独立线程。
- GET /status：返回 K230 状态 JSON（ip/rtsp_url/rtsp_running/state/audio_busy）
- POST /command：电脑端下发命令 {cmd:rtsp/speak/play_wav/led/exit,...}，入队主循环消费
- POST /face_result：电脑端推送识别结果 {label,known,score}，入队主循环消费→播报
- OPTIONS：CORS 预检

入队-消费模式：HTTP 线程只入队，主循环 pop_command/pop_face_result 执行（线程安全）。
移植自原项目 maix-dostudy-v1.0.1/status_server.py，适配学长方案。
"""

import _thread
import json


class HttpCmdServer:
    """轻量级 HTTP 命令/状态服务器"""

    # 响应头（单行字符串，避免 MicroPython 跨行字面量拼接问题）
    _HTTP_OK_PREFIX = "HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\nAccess-Control-Allow-Origin: *\r\nCache-Control: no-cache\r\nConnection: close\r\n"
    _HTTP_404 = "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    _HTTP_CORS_PREFLIGHT = "HTTP/1.1 204 No Content\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Allow-Methods: GET, POST, OPTIONS\r\nAccess-Control-Allow-Headers: Content-Type\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"

    def __init__(self, port=8001):
        self._port = port
        self._status = {
            "ip": "",
            "rtsp_url": "",
            "rtsp_running": False,
            "state": "空闲",
            "audio_busy": False,
        }
        self._lock = _thread.allocate_lock()
        self._running = False
        self._server_socket = None
        # 命令队列（POST /command）和识别结果队列（POST /face_result）
        self._cmd_queue = []
        self._face_result_queue = []
        # 预缓存 JSON 响应体（仅状态变化时重建）
        self._cached_body = b'{}'
        self._cached_body_len = 2
        self._last_fingerprint = ""
        print("[HTTP] 初始化完成，端口: " + str(port))

    def start(self):
        if self._running:
            return True
        self._running = True
        try:
            _thread.start_new_thread(self._serve, ())
            print("[HTTP] 已启动，http://0.0.0.0:" + str(self._port) + "/status")
            return True
        except Exception as e:
            print("[HTTP] 启动失败: " + str(e))
            self._running = False
            return False

    def stop(self):
        self._running = False
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
            self._server_socket = None
        print("[HTTP] 已停止")

    def update_status(self, data):
        """主循环调用，更新状态并按需重建 JSON 缓存（指纹变化检测）"""
        with self._lock:
            self._status.update(data)
            fp = (str(self._status.get("ip", "")) + "|"
                  + str(self._status.get("rtsp_url", "")) + "|"
                  + str(self._status.get("rtsp_running", False)) + "|"
                  + str(self._status.get("state", "")) + "|"
                  + str(self._status.get("audio_busy", False)))
            if fp != self._last_fingerprint:
                self._last_fingerprint = fp
                # MicroPython json.dumps 不支持 ensure_ascii 关键字，
                # 默认会把中文转义成 \uXXXX，电脑端 JSON 解析可正常还原。
                body = json.dumps(self._status)
                self._cached_body = body.encode('utf-8')
                self._cached_body_len = len(self._cached_body)

    def pop_command(self):
        """主循环调用：取出一条 POST /command 指令，无则 None"""
        with self._lock:
            if self._cmd_queue:
                return self._cmd_queue.pop(0)
            return None

    def pop_face_result(self):
        """主循环调用：取出一条 POST /face_result 识别结果，无则 None"""
        with self._lock:
            if self._face_result_queue:
                return self._face_result_queue.pop(0)
            return None

    def _serve(self):
        import socket
        try:
            self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_socket.bind(('0.0.0.0', self._port))
            self._server_socket.listen(3)
            self._server_socket.settimeout(0.5)
            print("[HTTP] 监听端口 " + str(self._port))
        except Exception as e:
            print("[HTTP] 绑定端口失败: " + str(e))
            self._running = False
            return

        while self._running:
            try:
                client = None
                try:
                    client, addr = self._server_socket.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    client.settimeout(2.0)
                    request = client.recv(1024).decode('utf-8', errors='ignore')
                    if request.startswith('GET /status'):
                        body_bytes = self._cached_body
                        body_len = self._cached_body_len
                        response = (self._HTTP_OK_PREFIX
                                    + "Content-Length: " + str(body_len) + "\r\n\r\n").encode('utf-8') + body_bytes
                        client.sendall(response)
                    elif request.startswith('OPTIONS'):
                        client.sendall(self._HTTP_CORS_PREFLIGHT.encode('utf-8'))
                    elif request.startswith('POST /command'):
                        self._handle_post(client, request, self._cmd_queue)
                    elif request.startswith('POST /face_result'):
                        self._handle_post(client, request, self._face_result_queue)
                    else:
                        client.sendall(self._HTTP_404.encode('utf-8'))
                except Exception:
                    pass
                finally:
                    try:
                        client.close()
                    except Exception:
                        pass
            except Exception:
                if not self._running:
                    break
        try:
            if self._server_socket:
                self._server_socket.close()
        except Exception:
            pass
        self._server_socket = None
        print("[HTTP] 服务线程已退出")

    def _handle_post(self, client, request, queue):
        """处理 POST /command 或 /face_result：读 body，解析 JSON，入队"""
        sep = request.find('\r\n\r\n')
        body = request[sep + 4:] if sep != -1 else ''
        content_length = 0
        for line in request.split('\r\n'):
            if line.lower().startswith('content-length:'):
                try:
                    content_length = int(line.split(':', 1)[1].strip())
                except Exception:
                    content_length = 0
                break
        try:
            while len(body.encode('utf-8')) < content_length:
                chunk = client.recv(512).decode('utf-8', errors='ignore')
                if not chunk:
                    break
                body += chunk
        except Exception:
            pass
        ok = False
        msg = 'bad json'
        try:
            payload = json.loads(body) if body else {}
            if isinstance(payload, dict):
                with self._lock:
                    queue.append(payload)
                ok = True
                msg = 'queued'
            else:
                msg = 'not dict'
        except Exception as e:
            msg = 'bad json: ' + str(e)
        resp_body = json.dumps({"ok": ok, "msg": msg}).encode('utf-8')
        response = (self._HTTP_OK_PREFIX
                    + "Content-Length: " + str(len(resp_body)) + "\r\n\r\n").encode('utf-8') + resp_body
        try:
            client.sendall(response)
        except Exception:
            pass

    def destroy(self):
        self.stop()
        print("[HTTP] 已销毁")
