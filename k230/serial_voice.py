"""
亚博智能 K230 人脸识别智能系统 - 串口语音模块通信（CanMV）

外接离线语音模块（SU-03T / ASRPRO 等）做语音识别，K230 通过 UART 收命令码、
发播报码。K230 完全不碰音频采集，根治本机 KWS 麦克风/卡顿问题（阶段 C-2）。

二进制协议（参考原项目 serial_comm.py）：
  ┌────────┬──────┬────────┬────────┬────────┬────────┐
  │ 帧头   │ cmd  │ len    │ data   │ checksum│ 帧尾   │
  │ AA 55  │ 1字节│ 1字节  │ N字节  │ 1字节   │ 55 AA  │
  └────────┴──────┴────────┴────────┴────────┴────────┘
  checksum = cmd ^ len ^ data[0] ^ ... ^ data[n-1]（异或，& 0xFF）

接收命令（语音模块 -> K230）：
  0x01 HOME            -> IDLE
  0x02 RECOGNIZE       -> RECOGNIZING
  0x03 ENROLL          -> ENROLLING
  0x04 STOP            -> IDLE
  0x05 RTSP_ON         开 RTSP 推流
  0x06 RTSP_OFF        关 RTSP 推流
  0x07 ENROLL_CAPTURE  录入当前帧

发送命令（K230 -> 语音模块，播报）：
  0x10 STATE_IDLE        待机
  0x11 STATE_RECOGNIZING 识别中
  0x12 STATE_ENROLLING   录入中
  0x20 FACE_KNOWN        熟人
  0x21 FACE_UNKNOWN      陌生人
  0x30 ENROLL_OK         录入成功
  0x31 ENROLL_FAIL       录入失败

接线（K230 YbUart 默认 UART1，TX=Pin9，RX=Pin10）：
  K230 Pin9 (TX)  -> 语音模块 RX
  K230 Pin10 (RX) -> 语音模块 TX
  GND 共地，波特率 115200（语音模块端需配同样波特率）
"""

import _thread
import time
from ybUtils.YbUart import YbUart


# ==================== 协议常量 ====================
FRAME_HEAD = b'\xAA\x55'
FRAME_TAIL = b'\x55\xAA'
MAX_BUFFER_SIZE = 1024


class RecvCmd:
    """接收命令码（语音模块 -> K230）"""
    HOME           = 0x01   # 回待机
    RECOGNIZE      = 0x02   # 开始识别
    ENROLL         = 0x03   # 开始录入
    STOP           = 0x04   # 停止/回待机
    RTSP_ON        = 0x05   # 开 RTSP
    RTSP_OFF       = 0x06   # 关 RTSP
    ENROLL_CAPTURE = 0x07   # 录入当前帧
    RTSP_TOGGLE    = 0x08   # 触摸推流按钮专用（toggle）；串口用 RTSP_ON/OFF
    WIFI_SETTINGS  = 0x09   # 进入/退出 WiFi 设置界面（触摸 WiFi 按钮）


class SendCmd:
    """发送命令码（K230 -> 语音模块，播报）"""
    STATE_IDLE        = 0x10
    STATE_RECOGNIZING = 0x11
    STATE_ENROLLING   = 0x12
    FACE_KNOWN        = 0x20
    FACE_UNKNOWN      = 0x21
    ENROLL_OK         = 0x30
    ENROLL_FAIL       = 0x31


RECV_CMD_NAMES = {
    RecvCmd.HOME: "HOME", RecvCmd.RECOGNIZE: "RECOGNIZE",
    RecvCmd.ENROLL: "ENROLL", RecvCmd.STOP: "STOP",
    RecvCmd.RTSP_ON: "RTSP_ON", RecvCmd.RTSP_OFF: "RTSP_OFF",
    RecvCmd.ENROLL_CAPTURE: "ENROLL_CAPTURE",
    RecvCmd.RTSP_TOGGLE: "RTSP_TOGGLE",
    RecvCmd.WIFI_SETTINGS: "WIFI_SETTINGS",
}


class SerialVoice:
    """串口语音模块通信管理器"""

    def __init__(self, baudrate=115200):
        self._baudrate = baudrate
        self._uart = None
        self._is_running = False
        self._callback = None
        self._buffer = bytearray()
        print(f"[串口语音] 模块初始化 (波特率:{baudrate})")

    def start(self, callback=None):
        """
        启动串口接收线程。
        callback(cmd_id, data): 收到完整命令时调用（接收线程上下文）。
        """
        if self._is_running:
            print("[串口语音] 已在运行")
            return True
        self._callback = callback
        try:
            # YbUart 默认 UART1，TX=Pin9，RX=Pin10
            self._uart = YbUart(baudrate=self._baudrate)
            self._is_running = True
            _thread.start_new_thread(self._rx_thread, ())
            print("[串口语音] 已启动，等待语音模块命令...")
            return True
        except Exception as e:
            print(f"[串口语音] 启动失败: {e}")
            self._is_running = False
            return False

    # ---------------- 帧解析 ----------------
    @staticmethod
    def _checksum(cmd, ln, data):
        c = cmd ^ ln
        for b in data:
            c ^= b
        return c & 0xFF

    def _parse_packet(self, packet):
        """解析 帧头/帧尾 之间的内容：cmd | len | data | checksum"""
        if len(packet) < 3:
            return None
        cmd = packet[0]
        ln = packet[1]
        if len(packet) < 2 + ln + 1:
            return None
        data = packet[2:2 + ln]
        checksum = packet[2 + ln]
        if self._checksum(cmd, ln, data) != checksum:
            print(f"[串口语音] 校验错: cmd={cmd:#04x}")
            return None
        return (cmd, bytes(data))

    def _process_buffer(self):
        """从缓冲区切出完整帧"""
        if len(self._buffer) > MAX_BUFFER_SIZE:
            self._buffer = self._buffer[-(MAX_BUFFER_SIZE // 2):]
        while True:
            head = self._buffer.find(FRAME_HEAD)
            if head == -1:
                self._buffer = self._buffer[-1:] if len(self._buffer) > 0 else self._buffer
                return
            if head > 0:
                self._buffer = self._buffer[head:]
            tail = self._buffer.find(FRAME_TAIL, len(FRAME_HEAD))
            if tail == -1:
                return
            packet = self._buffer[len(FRAME_HEAD):tail]
            result = self._parse_packet(packet)
            if result:
                cmd_id, data = result
                name = RECV_CMD_NAMES.get(cmd_id, f"未知({cmd_id:#04x})")
                print(f"[串口语音] 收到: {name} data={data}")
                if self._callback:
                    try:
                        self._callback(cmd_id, data)
                    except Exception as e:
                        print(f"[串口语音] 回调异常: {e}")
            self._buffer = self._buffer[tail + len(FRAME_TAIL):]

    def _rx_thread(self):
        print("[串口语音] 接收线程启动")
        while self._is_running:
            try:
                if self._uart is not None:
                    # YbUart.read 非阻塞读可用数据
                    data = self._uart.read()
                    if data:
                        self._buffer.extend(data)
                        self._process_buffer()
                    else:
                        time.sleep_ms(10)
                else:
                    time.sleep_ms(50)
            except Exception as e:
                print(f"[串口语音] 接收异常: {e}")
                time.sleep_ms(100)
        print("[串口语音] 接收线程退出")

    # ---------------- 发送 ----------------
    def send(self, cmd_id, data=b''):
        """发送一帧命令给语音模块"""
        if self._uart is None:
            return False
        try:
            ln = len(data)
            checksum = self._checksum(cmd_id, ln, data)
            pkt = bytearray()
            pkt.extend(FRAME_HEAD)
            pkt.append(cmd_id)
            pkt.append(ln)
            pkt.extend(data)
            pkt.append(checksum)
            pkt.extend(FRAME_TAIL)
            self._uart.write(bytes(pkt))
            return True
        except Exception as e:
            print(f"[串口语音] 发送失败: {e}")
            return False

    def send_state(self, state_id):
        """发送当前状态播报"""
        return self.send(state_id)

    def send_face_result(self, known):
        """发送人脸识别结果（True=熟人，False=陌生人）"""
        return self.send(SendCmd.FACE_KNOWN if known else SendCmd.FACE_UNKNOWN)

    def send_enroll_result(self, ok):
        """发送录入结果"""
        return self.send(SendCmd.ENROLL_OK if ok else SendCmd.ENROLL_FAIL)

    def is_running(self):
        return self._is_running

    def stop(self):
        self._is_running = False
        time.sleep_ms(100)

    def destroy(self):
        self.stop()
        try:
            if self._uart is not None:
                self._uart.deinit()
        except Exception:
            pass
        self._uart = None
        print("[串口语音] 资源已释放")


# ==================== 独立测试入口 ====================
if __name__ == "__main__":
    # 上板单独运行：测试串口收发。用 USB-TTL 接 Pin9/10 可自测回环。
    import os
    os.exitpoint(os.EXITPOINT_ENABLE)

    def on_cmd(cmd_id, data):
        print(f"  >>> [测试] cmd={cmd_id:#04x} data={data}")

    sv = SerialVoice(baudrate=115200)
    if not sv.start(callback=on_cmd):
        raise SystemExit

    print("=" * 50)
    print("串口语音测试：等待命令；每 5 秒发一次状态播报")
    print("=" * 50)

    cnt = 0
    try:
        while True:
            os.exitpoint()
            time.sleep_ms(5000)
            cnt += 1
            sv.send_state(SendCmd.STATE_IDLE if cnt % 2 else SendCmd.STATE_RECOGNIZING)
            print(f"[测试] 已发播报 #{cnt}")
    except KeyboardInterrupt:
        pass
    finally:
        sv.destroy()
