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


# ==================== 在线语音识别器（一次性录音模式） ====================

# 监听阶段
PHASE_IDLE = 0        # 空闲
PHASE_RECORDING = 1   # 录音中（麦克风打开，倒计时）
PHASE_UPLOADING = 2   # 上传识别中（麦克风已释放，等服务端回发）


class OnlineVoiceRecognition:
    """
    在线语音识别器（DashScope 一次性录音模式 one-shot）

    交互：按一下「语音」按钮 → 录固定时长（默认 4s）→ 释放麦克风
          → 连 WS 把整段音频快速发上去 → 收整句结果 → 关键词子串匹配 → 回调。

    与旧版流式的关键差异（也是修复 RTSP 冲突的核心）：
      - 旧版常驻会话线程长期占麦克风（PyAudio），与 VENC 硬编码互斥 → RTSP 起不来。
      - 新版麦克风只在那几秒短暂打开，录完立刻释放；网络阶段完全不碰音频。
      - 配合主程序「录音前暂停 RTSP、录完恢复」，两者彻底不冲突。

    用法：
        ovr = OnlineVoiceRecognition(api_key="sk-xxx")
        ovr.start(keywords={'主界面': 'home'}, callback=on_command)
        ovr.start_listening(timeout_ms=4000)   # 按钮触发，录 4 秒
        ...
        ovr.destroy()
    """

    def __init__(self, api_key, sample_rate=SAMPLE_RATE,
                 chunk_ms=AUDIO_CHUNK_MS, record_ms=4000,
                 reconnect_delay_ms=800):
        """
        参数:
            api_key: DashScope API Key
            sample_rate: 采样率（需与模型参数一致）
            chunk_ms: 每次采集的音频块时长（毫秒）
            record_ms: 单次录音时长（毫秒），按按钮后录这么久
            reconnect_delay_ms: 兼容旧签名，本模式未使用
        """
        self._api_key = api_key
        self._sample_rate = sample_rate
        self._record_ms = record_ms

        self._keywords = {}          # {中文文本: 命令名}
        self._callback = None        # 识别命中回调

        self._is_running = False     # 已 start（就绪可接受按钮触发）
        self._is_listening = False   # 一次会话进行中（录音或上传）
        self._phase = PHASE_IDLE
        self._record_start = 0       # 录音开始时刻（倒计时用）

        self._lock = _thread.allocate_lock()
        self._just_finished = False  # 本次会话刚结束（主循环消费一次）
        self._last_matched = None    # 本次会话命中的命令（None=未命中）
        self._last_text = ""         # 最近识别文本（调试/显示用）

        # PyAudio 录音对象
        self._pyaudio = None
        self._stream = None
        self._chunk_frames = int(self._sample_rate * chunk_ms / 1000)

        # 单次会话内的临时事件状态
        self._task_started = False
        self._session_active = False

        print("[在线语音] 模块初始化完成（一次性录音模式）")

    # ---------- 配置接口 ----------

    def set_keywords(self, keywords):
        """设置关键词字典 {中文文本: 命令名}"""
        self._keywords = keywords or {}
        print(f"[在线语音] 关键词: {len(self._keywords)} 个")

    def set_callback(self, callback):
        """设置识别命中回调，回调接收命令名字符串"""
        self._callback = callback

    def is_running(self):
        return self._is_running

    # ---------- 启停控制 ----------

    def start(self, keywords=None, callback=None):
        """登记关键词/回调，置为就绪态（不启动任何线程，等按钮触发）"""
        if keywords:
            self.set_keywords(keywords)
        if callback:
            self.set_callback(callback)
        if not self._keywords:
            print("[在线语音] 未设置关键词，启动失败")
            return False
        self._is_running = True
        print("[在线语音] 已就绪，关键词:")
        for text, cmd in self._keywords.items():
            print(f"  - {text} -> {cmd}")
        return True

    def stop(self):
        """停止：取消当前会话"""
        self._is_running = False
        self._is_listening = False
        self._session_active = False

    # ---------- 按钮触发监听控制 ----------

    def start_listening(self, timeout_ms=None):
        """按钮触发：开始一次录音+识别会话（录 record_ms 毫秒）"""
        if not self._is_running:
            print("[在线语音] 未就绪，无法开始")
            return False
        if self._is_listening:
            return True
        if timeout_ms is not None:
            self._record_ms = timeout_ms
        self._is_listening = True
        self._phase = PHASE_RECORDING
        self._record_start = time.ticks_ms()
        with self._lock:
            self._just_finished = False
            self._last_matched = None
            self._last_text = ""
        try:
            _thread.start_new_thread(self._worker, ())
        except Exception as e:
            print(f"[在线语音] 启动会话线程失败: {e}")
            self._is_listening = False
            self._phase = PHASE_IDLE
            return False
        print(f"[在线语音] 开始录音（{self._record_ms}ms）")
        return True

    def stop_listening(self):
        """取消当前会话（worker 线程下一拍自行退出并释放麦克风）"""
        if not self._is_listening:
            return
        self._is_listening = False
        print("[在线语音] 取消会话")

    def is_listening(self):
        return self._is_listening

    def check_timeout(self):
        """主循环调用：本模式 worker 线程自管时长，这里空实现（兼容旧调用点）"""
        return

    def get_phase(self):
        return self._phase

    def get_remaining_ms(self):
        """录音剩余毫秒（倒计时显示用）；非录音阶段返回 0"""
        if self._phase != PHASE_RECORDING:
            return 0
        elapsed = time.ticks_diff(time.ticks_ms(), self._record_start)
        return max(0, self._record_ms - elapsed)

    def poll_finished(self):
        """主循环调用：本次会话是否刚结束（返回 True 仅一次）"""
        with self._lock:
            f = self._just_finished
            self._just_finished = False
        return f

    def last_matched(self):
        """上次会话命中的命令名（None=未命中关键词）"""
        with self._lock:
            return self._last_matched

    def destroy(self):
        """销毁识别器"""
        self.stop()
        time.sleep_ms(100)
        self._teardown_recorder()
        print("[在线语音] 已销毁")

    # ---------- 一次性会话 worker ----------

    def _worker(self):
        """会话线程：录音 → 释放麦克风 → 上传识别 → 关键词匹配 → 标记结束"""
        matched = None
        text = ""
        try:
            pcm_buf = self._record_audio()
            # 录完立刻释放麦克风，后续网络阶段完全不碰音频资源
            self._teardown_recorder()
            if not self._is_listening:
                print("[在线语音] 会话被取消（录音阶段）")
            elif not pcm_buf:
                print("[在线语音] 未采到音频")
            else:
                self._phase = PHASE_UPLOADING
                matched, text = self._recognize(pcm_buf)
        except Exception as e:
            print(f"[在线语音] 会话异常: {e}")
        finally:
            self._teardown_recorder()
            with self._lock:
                self._last_matched = matched
                self._last_text = text
                self._just_finished = True
            self._phase = PHASE_IDLE
            self._is_listening = False
            # 命中则回调（worker 线程内回调，主程序里只做入队，线程安全）
            if matched and self._callback:
                try:
                    self._callback(matched)
                except Exception as e:
                    print(f"[在线语音] 回调异常: {e}")
            print(f"[在线语音] 会话结束 text='{text}' matched={matched}")

    def _record_audio(self):
        """录 record_ms 毫秒 PCM，返回音频块列表（被取消则返回已采部分）"""
        try:
            stream = self._ensure_recorder()
        except Exception as e:
            print(f"[在线语音] 麦克风初始化失败: {e}")
            return []
        buf = []
        start = time.ticks_ms()
        while self._is_listening and time.ticks_diff(time.ticks_ms(), start) < self._record_ms:
            try:
                pcm = stream.read()
            except Exception as e:
                print(f"[在线语音] 录音读取异常: {e}")
                break
            if pcm and len(pcm) > 0:
                buf.append(bytes(pcm))
        total = sum(len(b) for b in buf)
        print(f"[在线语音] 录音完成，{len(buf)} 块 共 {total}B")
        return buf

    def _recognize(self, pcm_buf):
        """
        把整段缓冲音频送 DashScope 识别，返回 (matched_command, full_text)。
        流程：连 WS → run-task → 等 task-started → 快速发完音频 → finish-task
              → 收 result-generated 整句 → 关键词匹配 → task-finished/close。
        """
        task_id = ubinascii.hexlify(os.urandom(16)).decode()
        self._task_started = False
        self._session_active = True
        ws = None
        matched = None
        final_text = ""
        try:
            # 1. WS 连接
            ws = _WSClient(
                host=WS_URL_HOST, port=WS_URL_PORT, path=WS_URL_PATH,
                headers={"Authorization": "bearer " + self._api_key},
            )
            ws.connect()
            print("[在线语音] WS 已连接，发送 run-task...")

            # 2. run-task
            ws.send_text(json.dumps({
                "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
                "payload": {
                    "task_group": "audio", "task": "asr", "function": "recognition",
                    "model": ASR_MODEL,
                    "parameters": {
                        "format": "pcm",
                        "sample_rate": self._sample_rate,
                        "language_hints": ["zh", "en"],
                    },
                    "input": {},
                },
            }))

            # 3. 等 task-started
            wait_start = time.ticks_ms()
            while not self._task_started and time.ticks_diff(time.ticks_ms(), wait_start) < 10000:
                opcode, payload = ws.recv()
                if opcode == OPCODE_CLOSE:
                    print("[在线语音] 等待 task-started 时连接关闭")
                    return None, ""
                if opcode == OPCODE_TEXT:
                    m = self._parse(payload)
                    ev = m.get("header", {}).get("event", "") if m else ""
                    if ev == "task-started":
                        self._task_started = True
                    elif ev == "task-failed":
                        h = m.get("header", {})
                        print(f"[在线语音] 任务失败: {h.get('error_code','')} {h.get('error_message','')}")
                        return None, ""
            if not self._task_started:
                print("[在线语音] 等待 task-started 超时")
                return None, ""

            # 4. 快速发完缓冲音频（略带节流，避免一次性灌爆服务端）
            print(f"[在线语音] 上传 {len(pcm_buf)} 块音频...")
            for chunk in pcm_buf:
                ws.send_binary(chunk)
                time.sleep_ms(10)

            # 5. finish-task 通知结束
            ws.send_text(json.dumps({
                "header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
                "payload": {"input": {}},
            }))

            # 6. 收结果直到 task-finished / 关闭
            ws.set_recv_timeout(6.0)
            collect_start = time.ticks_ms()
            while time.ticks_diff(time.ticks_ms(), collect_start) < 8000:
                try:
                    opcode, payload = ws.recv()
                except Exception as e:
                    print(f"[在线语音] 收结果结束: {e}")
                    break
                if opcode == OPCODE_CLOSE:
                    break
                if opcode != OPCODE_TEXT:
                    continue
                m = self._parse(payload)
                if not m:
                    continue
                ev = m.get("header", {}).get("event", "")
                if ev == "result-generated":
                    text, is_end = self._extract_sentence(m.get("payload", {}))
                    if text:
                        final_text = text
                        if is_end:
                            print(f"[在线语音] 识别: {text}")
                            cmd = self._match_keywords(text)
                            if cmd:
                                matched = cmd
                elif ev in ("task-finished", "task-failed"):
                    if ev == "task-failed":
                        h = m.get("header", {})
                        print(f"[在线语音] 任务失败: {h.get('error_code','')} {h.get('error_message','')}")
                    break
        except Exception as e:
            print(f"[在线语音] 识别异常: {e}")
        finally:
            self._session_active = False
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass
        return matched, final_text

    @staticmethod
    def _parse(payload):
        try:
            return json.loads(payload.decode("utf-8"))
        except Exception:
            return None

    @staticmethod
    def _extract_sentence(payload):
        """从 result-generated payload 取 (text, sentence_end)"""
        if not isinstance(payload, dict):
            return "", False
        sentence = payload.get("output", {}).get("sentence", {})
        if not isinstance(sentence, dict):
            return "", False
        return sentence.get("text", "") or "", bool(sentence.get("sentence_end", False))

    def _match_keywords(self, text):
        """子串匹配关键词，返回命中的命令名（None=未命中）"""
        for kw, command in self._keywords.items():
            if kw in text:
                print(f"[在线语音] 命中: {kw} -> {command}")
                return command
        return None

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

    ovr = OnlineVoiceRecognition(api_key=API_KEY, record_ms=4000)
    ovr.start(keywords=TEST_KEYWORDS, callback=on_command)

    print("=" * 50)
    print("在线语音识别测试（一次性录音模式）")
    print("每隔几秒自动录 4 秒并识别，请对着麦克风说话...")
    print("（Ctrl+C 退出）")
    print("=" * 50)

    try:
        while True:
            os.exitpoint()
            if not ovr.is_listening():
                ovr.start_listening(timeout_ms=4000)
            time.sleep_ms(200)
    except KeyboardInterrupt:
        pass
    finally:
        ovr.destroy()
        try:
            MediaManager.deinit()
        except Exception:
            pass
