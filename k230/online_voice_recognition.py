# -*- coding: utf-8 -*-
"""
亚博智能 K230 人脸识别智能系统 - 在线语音识别模块（DashScope paraformer-realtime-v2，CanMV）

移植自 maix-dostudy-v1.0.1/online_voice_recognition.py（MaixPy4 版）。

与 MaixPy4 版的差异：
  - MaixPy4 用 CPython websocket-client 包；CanMV MicroPython 无此包，故内嵌最小
    RFC 6455 WebSocket 客户端（socket+ssl，文本/二进制帧，ping/pong/close，带 send 锁）。
  - MaixPy4 用 maix.audio.Recorder；K230 用 media.pyaudio（与 voice_recognition.py 一致）。
  - app.need_exit() -> os.exitpoint()；uuid -> ubinascii.hexlify(os.urandom)。

功能：
  - 麦克风实时采 PCM -> WebSocket 流式上传阿里云 DashScope（100ms 一块二进制帧）
  - paraformer-realtime-v2 实时返回中文识别文本（sentence.text + sentence_end）
  - 中文子串关键词匹配，命中触发命令回调（带 TRIGGER_DEBOUNCE_MS 去抖）
  - 会话断开后自动重连（reconnect_delay_ms 间隔）
  - 暂停判断回调（pause_callback）：按状态让出麦克风/WS（时分复用）
"""

import json
import os
import ssl
import socket
import struct
import time
import _thread
import ubinascii
import select

from media.pyaudio import PyAudio, paInt16


# ==================== 配置常量 ====================

WS_URL_HOST = "dashscope.aliyuncs.com"
WS_URL_PORT = 443
WS_URL_PATH = "/api-ws/v1/inference/"

ASR_MODEL = "paraformer-realtime-v2"

SAMPLE_RATE = 16000
AUDIO_CHANNEL = 1
AUDIO_CHUNK_MS = 100         # 每次采集/上传的音频块时长（毫秒）
TRIGGER_DEBOUNCE_MS = 1500   # 同一命令的去抖时间（毫秒）


# ==================== 最小 RFC 6455 WebSocket 客户端 ====================

OPCODE_TEXT = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA


class _WSClient:
    """
    最小 WebSocket 客户端（RFC 6455）。仅支持在线语音识别所需子集：
    文本/二进制帧、MASK=1、自动 pong、close、无扩展/分片。
    send_* 内部加锁，支持两个线程并发发送（会话线程发 JSON，音频线程发 PCM）。
    """

    def __init__(self, host, port, path, headers=None):
        self._host = host
        self._port = port
        self._path = path
        self._headers = headers or {}
        self._sock = None
        self._raw_sock = None   # 保存原始 socket，用于 settimeout
        self._send_lock = _thread.allocate_lock()

    def connect(self):
        """TCP 连接 + TLS 握手 + HTTP Upgrade"""
        addr = socket.getaddrinfo(self._host, self._port, socket.AF_INET)[0][-1]
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            raw.settimeout(10)
        except Exception:
            pass
        raw.connect(addr)
        self._raw_sock = raw
        # TLS 握手。跳过证书校验（板子常无 CA 根证书，DashScope 用公网 CA）
        try:
            self._sock = ssl.wrap_socket(
                raw, server_hostname=self._host, cert_reqs=ssl.CERT_NONE)
        except TypeError:
            # 旧版 MicroPython 无 server_hostname 参数，退回无 SNI
            self._sock = ssl.wrap_socket(raw, cert_reqs=ssl.CERT_NONE)
        self._handshake()

    def set_recv_timeout(self, timeout_sec):
        """设置 recv 超时（秒），用于单线程收发交替时非阻塞尝试收帧"""
        try:
            self._raw_sock.settimeout(timeout_sec)
        except Exception:
            pass

    def _handshake(self):
        """HTTP Upgrade -> websocket，校验 101 状态码"""
        key_b64 = ubinascii.b2a_base64(os.urandom(16)).decode().strip()
        lines = [
            "GET " + self._path + " HTTP/1.1",
            "Host: " + self._host,
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Version: 13",
            "Sec-WebSocket-Key: " + key_b64,
        ]
        for k, v in self._headers.items():
            lines.append(k + ": " + v)
        lines.append("")
        lines.append("")
        self._write_all("\r\n".join(lines).encode("utf-8"))
        # 读响应直到 \r\n\r\n
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self._sock.read(1024)
            if not chunk:
                raise RuntimeError("WS 握手被对端关闭")
            resp += chunk
        status = resp.split(b"\r\n", 1)[0].decode("utf-8", "ignore")
        if "101" not in status:
            raise RuntimeError("WS 握手失败: " + status)

    def _write_all(self, data):
        """循环写直到所有字节发出（ssl.write 可能不一次写完）"""
        total = len(data)
        offset = 0
        writes = 0
        while offset < total:
            written = self._sock.write(data[offset:] if offset else data)
            if written is None:
                # 部分 MicroPython 的 write 成功时返回 None（表示全部写完）
                break
            if written == 0:
                raise RuntimeError("WS write 返回 0")
            offset += written
            writes += 1
        # 诊断：如果一次写不完，打印警告（前 20 次）
        if writes > 1 and getattr(self, '_write_dbg_cnt', 0) < 20:
            self._write_dbg_cnt = getattr(self, '_write_dbg_cnt', 0) + 1
            print(f"[在线语音 dbg] write_all: {total}B 需要 {writes} 次写完")

    def _send_frame(self, payload, opcode):
        """发一帧（MASK=1），必须持 _send_lock 调用"""
        b0 = 0x80 | opcode   # FIN=1
        n = len(payload)
        if n < 126:
            hdr = bytes([b0, 0x80 | n])
        elif n < 65536:
            hdr = bytes([b0, 0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr = bytes([b0, 0x80 | 127]) + struct.pack(">Q", n)
        mask_key = os.urandom(4)
        masked = bytearray(payload)
        for i in range(len(masked)):
            masked[i] ^= mask_key[i & 3]
        # 拼成完整帧后一次写完（ssl.write 可能不一次写完，必须循环）
        frame = hdr + mask_key + bytes(masked)
        self._write_all(frame)

    def _recv_exact(self, n):
        buf = b""
        empty_count = 0
        while len(buf) < n:
            chunk = self._sock.read(n - len(buf))
            if not chunk:
                empty_count += 1
                # MicroPython ssl.read 超时时可能返回 b""（不是 raise OSError）
                # 重试几次，区分真 EOF 和超时
                if empty_count >= 3:
                    raise RuntimeError("WS 收帧关闭")
                time.sleep_ms(10)
                continue
            empty_count = 0
            buf += chunk
        return buf

    def _recv_frame(self):
        hdr = self._recv_exact(2)
        b0, b1 = hdr[0], hdr[1]
        opcode = b0 & 0x0F
        masked = (b1 >> 7) & 1
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(8))[0]
        mask_key = self._recv_exact(4) if masked else None
        payload = self._recv_exact(length)
        if masked:
            payload = bytearray(payload)
            for i in range(len(payload)):
                payload[i] ^= mask_key[i & 3]
            payload = bytes(payload)
        return opcode, payload

    def send_text(self, text):
        """发文本帧（线程安全）"""
        with self._send_lock:
            self._send_frame(text.encode("utf-8"), OPCODE_TEXT)

    def send_binary(self, data):
        """发二进制帧（线程安全）"""
        with self._send_lock:
            self._send_frame(data, OPCODE_BINARY)

    def recv(self):
        """
        收一帧（阻塞）。自动处理 ping/close。
        返回 (opcode, payload)，其中 opcode 为 OPCODE_TEXT/OPCODE_BINARY/OPCODE_CLOSE。
        """
        while True:
            opcode, payload = self._recv_frame()
            if opcode in (OPCODE_TEXT, OPCODE_BINARY):
                return opcode, payload
            if opcode == OPCODE_PING:
                # 自动 pong（同 payload）
                try:
                    with self._send_lock:
                        self._send_frame(payload, OPCODE_PONG)
                except Exception:
                    pass
                continue
            if opcode == OPCODE_CLOSE:
                return OPCODE_CLOSE, payload
            # pong / continuation 忽略

    def close(self):
        try:
            with self._send_lock:
                self._send_frame(b"", OPCODE_CLOSE)
        except Exception:
            pass
        try:
            self._sock.close()
        except Exception:
            pass


# ==================== 在线语音识别器 ====================

class OnlineVoiceRecognition:
    """
    在线语音识别器（DashScope 实时流式）

    用法：
        ovr = OnlineVoiceRecognition(api_key="sk-xxx")
        ovr.set_pause_callback(should_pause)
        ovr.start(keywords={'主界面': 'home'}, callback=on_command)
        ...
        ovr.destroy()
    """

    def __init__(self, api_key, sample_rate=SAMPLE_RATE,
                 chunk_ms=AUDIO_CHUNK_MS,
                 pause_sleep_ms=200, reconnect_delay_ms=800):
        """
        参数:
            api_key: DashScope API Key
            sample_rate: 采样率（需与模型参数一致）
            chunk_ms: 每次采集/上传的音频块时长（毫秒）
            pause_sleep_ms: 暂停状态下的轮询睡眠时长
            reconnect_delay_ms: 会话断开后的重连等待时长
        """
        self._api_key = api_key
        self._sample_rate = sample_rate
        self._pause_sleep_ms = pause_sleep_ms
        self._reconnect_delay_ms = reconnect_delay_ms

        self._keywords = {}          # {中文文本: 命令名}
        self._callback = None        # 识别命中回调
        self._pause_callback = None  # 暂停判断回调（外部状态暂停，如识别态让出麦克风）

        self._is_running = False
        self._is_listening = False   # 按钮触发监听状态（False=暂停，True=监听中）
        self._listen_start_time = 0
        self._listen_timeout_ms = 8000  # 默认 8 秒超时（从 WS 就绪后开始计时）
        self._ws_ready = False          # WS 连接 + task-started 后才为 True

        # 当前会话对象（每次重连重建）
        self._ws = None
        self._task_id = None
        self._task_started = False
        self._session_active = False

        # PyAudio 录音对象
        self._pyaudio = None
        self._stream = None
        self._chunk_frames = int(self._sample_rate * chunk_ms / 1000)

        # 命令去抖 {命令名: 上次触发时间ms}
        self._last_trigger = {}

        print("[在线语音] 模块初始化完成")

    # ---------- 配置接口 ----------

    def set_keywords(self, keywords):
        """设置关键词字典 {中文文本: 命令名}"""
        self._keywords = keywords or {}
        print(f"[在线语音] 关键词: {len(self._keywords)} 个")

    def set_callback(self, callback):
        """设置识别命中回调，回调接收命令名字符串"""
        self._callback = callback

    def set_pause_callback(self, callback):
        """设置暂停判断回调，返回 True 时暂停采集上传"""
        self._pause_callback = callback

    def is_running(self):
        return self._is_running

    # ---------- 启停控制 ----------

    def start(self, keywords=None, callback=None):
        """启动在线语音识别"""
        if self._is_running:
            return True
        if keywords:
            self.set_keywords(keywords)
        if callback:
            self.set_callback(callback)
        if not self._keywords:
            print("[在线语音] 未设置关键词，启动失败")
            return False
        try:
            self._is_running = True
            _thread.start_new_thread(self._session_loop, ())
            print("[在线语音] 已启动，关键词:")
            for text, cmd in self._keywords.items():
                print(f"  - {text} -> {cmd}")
            return True
        except Exception as e:
            print(f"[在线语音] 启动失败: {e}")
            self._is_running = False
            return False

    def stop(self):
        """停止在线语音识别"""
        if not self._is_running:
            return
        self._is_running = False
        self._is_listening = False
        self._close_session()
        time.sleep_ms(200)
        print("[在线语音] 已停止")

    # ---------- 按钮触发监听控制 ----------

    def start_listening(self, timeout_ms=None):
        """按钮触发：开始监听（默认暂停，按需启动）"""
        if not self._is_running:
            print("[在线语音] 未运行，无法开始监听")
            return False
        if self._is_listening:
            return True
        self._is_listening = True
        self._ws_ready = False          # 等 WS 连接成功后才计时
        self._listen_start_time = 0     # task-started 时才设值
        if timeout_ms is not None:
            self._listen_timeout_ms = timeout_ms
        print(f"[在线语音] 开始监听（超时 {self._listen_timeout_ms}ms，WS 就绪后计时）")
        return True

    def stop_listening(self):
        """按钮触发/超时/命中后：停止监听（会话线程自动暂停释放麦克风）"""
        if not self._is_listening:
            return
        self._is_listening = False
        print("[在线语音] 停止监听（暂停释放麦克风）")

    def is_listening(self):
        return self._is_listening

    def check_timeout(self):
        """主循环调用：超时自动停止监听（仅 WS 就绪后才计时）"""
        if not self._is_listening:
            return
        if not self._ws_ready:
            return   # WS 还在连接中，不计时
        if time.ticks_diff(time.ticks_ms(), self._listen_start_time) >= self._listen_timeout_ms:
            print(f"[在线语音] 监听超时（{self._listen_timeout_ms}ms），自动停止")
            self.stop_listening()

    def get_remaining_ms(self):
        """返回剩余监听时间（毫秒），未就绪或未监听返回 0"""
        if not self._is_listening or not self._ws_ready:
            return 0
        elapsed = time.ticks_diff(time.ticks_ms(), self._listen_start_time)
        remaining = self._listen_timeout_ms - elapsed
        return max(0, remaining)

    def destroy(self):
        """销毁识别器"""
        self.stop()
        self._teardown_recorder()
        print("[在线语音] 已销毁")

    # ---------- 暂停判断 ----------

    def _is_paused(self):
        """暂停判断：未监听时暂停，或外部暂停回调返回 True"""
        if not self._is_listening:
            return True
        if self._pause_callback:
            try:
                return bool(self._pause_callback())
            except Exception:
                pass
        return False

    # ---------- 会话主循环 ----------

    def _session_loop(self):
        """会话调度线程：暂停时释放麦克风等待，否则建立识别会话并自动重连"""
        print("[在线语音] 会话线程启动")
        while self._is_running:
            if self._is_paused():
                self._teardown_recorder()
                time.sleep_ms(self._pause_sleep_ms)
                continue
            try:
                self._run_one_session()
            except Exception as e:
                print(f"[在线语音] 会话异常: {e}")
            if self._is_running:
                time.sleep_ms(self._reconnect_delay_ms)
        self._teardown_recorder()
        print("[在线语音] 会话线程退出")

    def _run_one_session(self):
        """
        建立并运行一次完整的识别会话。
        单线程收发交替方案：避免 MicroPython SSLSocket 并发读写问题。
        流程：开麦 → 连接 → run-task → 等 task-started → 立刻发音频 → 循环(采PCM→发→短超时收)
        """
        # MicroPython 无 uuid 模块，用 urandom 生成 task_id
        self._task_id = ubinascii.hexlify(os.urandom(16)).decode()
        self._task_started = False
        self._session_active = True

        # 0. 先开麦（PyAudio 初始化慢，提前开好，避免 task-started 后服务器等太久）
        try:
            stream = self._ensure_recorder()
        except Exception as e:
            print(f"[在线语音] 麦克风初始化失败: {e}")
            self._session_active = False
            return

        # 1. WS 连接
        try:
            self._ws = _WSClient(
                host=WS_URL_HOST, port=WS_URL_PORT, path=WS_URL_PATH,
                headers={"Authorization": "bearer " + self._api_key},
            )
            self._ws.connect()
            print("[在线语音] WS 已连接，发送 run-task...")
        except Exception as e:
            print(f"[在线语音] WS 连接失败: {e}")
            self._session_active = False
            self._ws = None
            return

        # 2. 发送 run-task
        try:
            self._ws.send_text(json.dumps({
                "header": {
                    "action": "run-task",
                    "task_id": self._task_id,
                    "streaming": "duplex",
                },
                "payload": {
                    "task_group": "audio",
                    "task": "asr",
                    "function": "recognition",
                    "model": ASR_MODEL,
                    "parameters": {
                        "format": "pcm",
                        "sample_rate": self._sample_rate,
                        "language_hints": ["zh", "en"],
                    },
                    "input": {},
                },
            }))
        except Exception as e:
            print(f"[在线语音] 发送 run-task 失败: {e}")
            self._close_session()
            return

        # 3. 等待 task-started（阻塞收帧，单线程安全）
        wait_start = time.ticks_ms()
        while (not self._task_started and self._session_active
               and self._is_running
               and time.ticks_diff(time.ticks_ms(), wait_start) < 10000):
            try:
                opcode, payload = self._ws.recv()
            except Exception as e:
                print(f"[在线语音] 等待 task-started 异常: {e}")
                self._close_session()
                return
            if opcode == OPCODE_CLOSE:
                print("[在线语音] 等待 task-started 时连接关闭")
                self._close_session()
                return
            if opcode == OPCODE_TEXT:
                try:
                    msg = json.loads(payload.decode("utf-8"))
                except Exception:
                    continue
                # 诊断：打印等待阶段收到的所有事件
                event_name = msg.get("header", {}).get("event", "unknown") if isinstance(msg, dict) else "unknown"
                print(f"[在线语音 dbg] 等待阶段收到事件: {event_name}")
                self._dispatch_event(msg)
                # 如果是 task-failed，_close_session 已被调用，退出
                if not self._session_active:
                    return

        if not self._task_started:
            print("[在线语音] 等待 task-started 超时")
            self._close_session()
            return

        # 4. 主循环：立刻发音频，用 select 非阻塞检查是否有服务器数据
        #    不用 settimeout（MicroPython ssl.read 超时时可能返回 b"" 被误判为 EOF）
        print("[在线语音] 开始上传音频")
        chunk_count = 0
        while self._session_active and self._is_running:
            if self._is_paused():
                print("[在线语音] 进入暂停，结束本次会话")
                break

            # 4a. 采集并发送音频
            try:
                pcm = stream.read()
                pcm_len = len(pcm) if pcm else 0
                if chunk_count < 10:
                    print(f"[在线语音 dbg] chunk#{chunk_count} pcm_len={pcm_len}")
                if pcm and pcm_len > 0:
                    self._ws.send_binary(pcm)
                    chunk_count += 1
                    if chunk_count % 50 == 0:
                        print(f"[在线语音] 已上传 {chunk_count} 块 ({pcm_len}B/块)")
                else:
                    if chunk_count < 10:
                        print(f"[在线语音] 警告: 麦克风返回空数据 pcm={pcm}")
            except Exception as e:
                print(f"[在线语音] 音频上传异常: {e}")
                break

            # 4b. 用 select 非阻塞检查原始 socket 是否有数据可读
            #     注意：SSL 层可能有内部缓冲，select 看不到；每 10 块强制收一次兜底
            try:
                readable, _, _ = select.select([self._ws._raw_sock], [], [], 0)
                should_recv = bool(readable) or (chunk_count % 3 == 0 and chunk_count > 0)
            except Exception:
                should_recv = (chunk_count % 3 == 0 and chunk_count > 0)

            if should_recv:
                try:
                    # 给 SSL 一个短超时，避免阻塞太久（100ms，不影响音频节奏）
                    self._ws.set_recv_timeout(0.1)
                    opcode, payload = self._ws.recv()
                    if chunk_count <= 20:
                        payload_preview = ""
                        if opcode == OPCODE_TEXT and len(payload) < 500:
                            payload_preview = payload.decode("utf-8", "ignore")[:200]
                        print(f"[在线语音 dbg] recv opcode={opcode} len={len(payload)} {payload_preview}")
                    if opcode == OPCODE_CLOSE:
                        print("[在线语音] 服务端关闭连接")
                        break
                    if opcode == OPCODE_TEXT:
                        try:
                            msg = json.loads(payload.decode("utf-8"))
                        except Exception:
                            continue
                        self._dispatch_event(msg)
                        if not self._session_active:
                            break
                except OSError:
                    # 超时：SSL 层无完整数据，正常继续
                    pass
                except Exception as e:
                    print(f"[在线语音] 收帧异常: {e}")
                    break

        # 5. 发 finish-task 通知服务端结束
        try:
            self._ws.send_text(json.dumps({
                "header": {
                    "action": "finish-task",
                    "task_id": self._task_id,
                    "streaming": "duplex",
                },
                "payload": {"input": {}},
            }))
        except Exception:
            pass

        self._close_session()

    def _dispatch_event(self, msg):
        """解析服务端事件（task-started / result-generated / task-finished / task-failed）"""
        header = msg.get("header", {}) if isinstance(msg, dict) else {}
        event = header.get("event", "")
        if event == "task-started":
            self._task_started = True
            self._ws_ready = True
            self._listen_start_time = time.ticks_ms()   # 从 WS 就绪开始计时
            print("[在线语音] 服务端就绪，开始上传音频")
        elif event == "result-generated":
            self._handle_result(msg.get("payload", {}))
        elif event == "task-finished":
            print("[在线语音] 任务完成")
            self._close_session()
        elif event == "task-failed":
            code = header.get("error_code", "")
            text = header.get("error_message", "")
            print(f"[在线语音] 任务失败: {code} {text}")
            self._close_session()

    def _close_session(self):
        self._session_active = False
        ws = self._ws
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        self._ws = None

    # ---------- 结果处理与关键词匹配 ----------

    def _handle_result(self, payload):
        """整句结束时做关键词匹配"""
        if not isinstance(payload, dict):
            return
        sentence = payload.get("output", {}).get("sentence", {})
        if not isinstance(sentence, dict):
            return
        text = sentence.get("text", "") or ""
        is_end = bool(sentence.get("sentence_end", False))
        if not text:
            return
        if is_end:
            print(f"[在线语音] 识别: {text}")
            self._match_keywords(text)

    def _match_keywords(self, text):
        """在识别文本中匹配关键词（子串匹配），去抖后触发回调"""
        now = time.ticks_ms()
        for kw, command in self._keywords.items():
            if kw in text:
                last = self._last_trigger.get(command, 0)
                if time.ticks_diff(now, last) < TRIGGER_DEBOUNCE_MS:
                    continue
                self._last_trigger[command] = now
                print(f"[在线语音] 命中: {kw} -> {command}")
                self.stop_listening()   # 命中即停，释放麦克风
                if self._callback:
                    try:
                        self._callback(command)
                    except Exception as e:
                        print(f"[在线语音] 回调异常: {e}")
                break  # 一句话只触发一个命令

    # ---------- 音频采集与上传 ----------

    def _ensure_recorder(self):
        """惰性创建麦克风录音对象（PyAudio + stream）"""
        if self._stream is None:
            self._pyaudio = PyAudio()
            self._pyaudio.initialize(self._chunk_frames)
            self._stream = self._pyaudio.open(
                format=paInt16, channels=AUDIO_CHANNEL,
                rate=self._sample_rate, input=True,
                frames_per_buffer=self._chunk_frames)
            try:
                self._stream.volume(vol=100)
            except Exception:
                pass
            print(f"[在线语音] 麦克风已打开 (rate={self._sample_rate} chunk={self._chunk_frames})")
        return self._stream

    def _teardown_recorder(self):
        """释放麦克风录音对象"""
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pyaudio is not None:
            try:
                self._pyaudio.terminate()
            except Exception:
                pass
            self._pyaudio = None


# ==================== 独立测试入口 ====================

if __name__ == "__main__":
    # 上板单独运行：验证 DashScope 流式识别通路
    from media.media import MediaManager

    API_KEY = "sk-dc4553ef7fe74c5283f05e4dc7d60adb"

    TEST_KEYWORDS = {
        "主界面": "home",
        "回到主页": "home",
        "录入": "enroll",
        "开始识别": "recognize",
        "停止": "stop",
        "设置": "settings",
    }

    def on_command(cmd):
        print(f"  >>> [测试] 触发命令: {cmd}")

    # 独立运行需手动 MediaManager.init 才能用 PyAudio
    MediaManager.init()

    ovr = OnlineVoiceRecognition(api_key=API_KEY)
    ovr.start(keywords=TEST_KEYWORDS, callback=on_command)

    print("=" * 50)
    print("在线语音识别测试中，请对着麦克风说话...")
    print("（Ctrl+C 退出）")
    print("=" * 50)

    try:
        while True:
            os.exitpoint()
            time.sleep_ms(200)
    except KeyboardInterrupt:
        pass
    finally:
        ovr.destroy()
        try:
            MediaManager.deinit()
        except Exception:
            pass
