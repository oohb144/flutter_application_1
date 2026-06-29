"""
亚博智能 K230 人脸识别智能系统 - 主程序（CanMV）

阶段 1：核心人脸识别 + 按键切换 + LED + LCD 显示
  - 开机进 RECOGNIZING 状态，实时检测+识别人脸，画框+标签
  - 长按(≥1.5s)：RECOGNIZING <-> ENROLLING 切换
  - 录入态短按：录入当前帧第一张人脸
  - 超长按(≥3s)：退出
  - LED 随状态变色（YbRGB）
  - LCD 显示画面 + 人脸框 + 状态提示

后续阶段（RTSP/HTTP/录制/语音）在状态机与按键回调中预留接口。
"""

import os
import gc
import time
import _thread

from media.media import *
from libs.PipeLine import PipeLine, ScopedTiming

import config
from state_machine import StateMachine
from key_manager import KeyManager
from led_controller import LedController
from face_detector import FaceDetector
from wifi_manager import connect_wifi, disconnect_wifi
from rtsp_manager import RtspManager
from http_cmd_server import HttpCmdServer


class FaceRecognitionApp:
    """主应用"""

    def __init__(self):
        # 硬件/管线
        self._pl = None
        self._sensor = None
        # 业务模块
        self._face = None
        self._key = None
        self._led = None
        self._sm = StateMachine(initial_state=int(config.State.IDLE))

        # 网络/推流
        self._wlan = None
        self._ip = None
        self._rtsp = None

        # HTTP 命令服务（电脑端联机：GET /status、POST /command、POST /face_result）
        self._http = None

        # 串口语音
        self._serial = None
        self._send_cmd = None
        self._cmd_queue = []
        self._cmd_lock = _thread.allocate_lock()

        # 触摸屏 UI
        self._touch = None

        # 运行时状态
        self._exit_flag = False
        self._last_faces = []          # 缓存上次识别结果（节流用）
        self._last_labeled = []        # 上次完整识别（带 label）的结果
        self._last_recog_time = 0      # 上次完整识别时间
        self._toast_msg = ""           # 浮层提示信息（录入/RTSP 等）
        self._toast_time = 0
        self._frame_count = 0

    # ---------- 初始化 ----------
    def _init_pipeline(self):
        from media.sensor import Sensor
        rgb888p_size = [config.RGB888P_WIDTH, config.RGB888P_HEIGHT]
        display_size = [config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT]
        # 自建 sensor 传给 PipeLine，便于 RtspManager 复用绑 VENC
        self._sensor = Sensor()
        self._pl = PipeLine(
            rgb888p_size=rgb888p_size,
            display_size=display_size,
            display_mode="lcd",       # 豪华版 ST7701
            osd_layer_num=2,          # 改为 layer 2，规避 layer 1 rotation -1 报错
            debug_mode=0,
        )
        # WBCRtsp 必须在 pl.create() 之前配置 VO writeback 通路（否则推流卡死）
        RtspManager.configure_before_pipeline()
        self._pl.create(sensor=self._sensor, to_ide=False,
                        fps=getattr(config, 'RTSP_FPS', 30))  # 帧率走配置，降帧整体减压

    def _connect_wifi(self):
        print("[主] 连接 WiFi...")
        ip, wlan = connect_wifi()
        self._ip = ip
        self._wlan = wlan
        if ip:
            self._set_toast(f"WiFi已连 IP:{ip}")
        else:
            self._set_toast("WiFi连接失败")

    def _init_modules(self):
        print("[主] 初始化 FaceDetector...")
        self._face = FaceDetector(
            faces_db_path=config.FACES_DB_DIR,
            conf_th=config.FACE_CONF_THRESHOLD,
            iou_th=config.FACE_IOU_THRESHOLD,
            recognize_th=config.FACE_RECOGNIZE_THRESHOLD,
            use_alignment=True,
        )
        print("[主] FaceDetector 完成")
        print("[主] 初始化 LedController...")
        self._led = LedController()
        print("[主] LedController 完成")
        print("[主] 初始化 KeyManager...")
        self._key = KeyManager(
            on_short_press=self._on_short_press,
            on_long_press=self._on_long_press,
            on_exit_press=self._on_exit_press,
        )
        print("[主] KeyManager 完成")
        print("[主] 初始化 SerialVoice...")
        if config.SERIAL_ENABLE:
            from serial_voice import SerialVoice, SendCmd
            try:
                self._serial = SerialVoice(baudrate=getattr(config, "SERIAL_BAUDRATE", 115200))
                self._serial.start(callback=self._on_command)
                self._send_cmd = SendCmd
            except Exception as e:
                print(f"[主] 串口语音初始化失败（不影响按键/识别）: {e}")
                self._serial = None
                self._send_cmd = None
        else:
            print("[主] 串口语音未启用 (SERIAL_ENABLE=False)")
        print("[主] SerialVoice 完成")
        print("[主] 初始化 TouchUI...")
        if config.TOUCH_ENABLE:
            try:
                from touch_ui import TouchUI
                self._touch = TouchUI(on_command=self._on_command)
            except Exception as e:
                print(f"[主] 触摸UI初始化失败（不影响按键/识别）: {e}")
                self._touch = None
        else:
            print("[主] 触摸UI未启用 (TOUCH_ENABLE=False)")
        print("[主] TouchUI 完成")

    def _register_states(self):
        """注册状态机 handler 与进/出回调"""
        self._sm.register_handler(int(config.State.IDLE), self._handle_idle)
        self._sm.register_handler(int(config.State.RECOGNIZING), self._handle_recognizing)
        self._sm.register_handler(int(config.State.ENROLLING), self._handle_enrolling)

        self._sm.register_enter_callback(int(config.State.IDLE), self._on_enter_idle)
        self._sm.register_enter_callback(int(config.State.RECOGNIZING), self._on_enter_recognizing)
        self._sm.register_enter_callback(int(config.State.ENROLLING), self._on_enter_enrolling)
        self._sm.register_exit_callback(int(config.State.IDLE), self._on_exit_idle)
        self._sm.register_exit_callback(int(config.State.RECOGNIZING), self._on_exit_recognizing)
        self._sm.register_exit_callback(int(config.State.ENROLLING), self._on_exit_enrolling)

    def _init_http(self):
        """启动 HTTP 命令服务（:8001），供电脑端查状态/下发命令/推送识别结果"""
        try:
            self._http = HttpCmdServer(port=config.STATUS_SERVER_PORT)
            self._http.start()
            # 首次上报一次状态（ip/rtsp_url 等），电脑端连上即可见
            self._update_http_status()
        except Exception as e:
            print(f"[主] HTTP 服务初始化失败（不影响本机识别）: {e}")
            self._http = None

    def _update_http_status(self):
        """主循环调用：把当前状态上报给 HTTP 服务（指纹变化才重建 JSON）"""
        if self._http is None:
            return
        try:
            state_name = config.STATE_NAMES.get(self._sm.state, "空闲")
            rtsp_running = self._rtsp is not None and self._rtsp.is_running()
            rtsp_url = self._rtsp.get_url() if self._rtsp else ""
            self._http.update_status({
                "ip": self._ip or "",
                "rtsp_url": rtsp_url,
                "rtsp_running": rtsp_running,
                "state": state_name,
                "audio_busy": False,
            })
        except Exception as e:
            print(f"[主] 状态上报异常: {e}")

    def _process_http_commands(self):
        """主循环调用：取出并执行 HTTP /command 队列（主线程安全）"""
        if self._http is None:
            return
        while True:
            payload = self._http.pop_command()
            if payload is None:
                return
            try:
                self._dispatch_http_command(payload)
            except Exception as e:
                print(f"[主] HTTP 命令执行异常: {e}")

    def _process_face_results(self):
        """主循环调用：取出并执行 HTTP /face_result 队列→触发语音播报"""
        if self._http is None:
            return
        while True:
            payload = self._http.pop_face_result()
            if payload is None:
                return
            try:
                self._dispatch_face_result(payload)
            except Exception as e:
                print(f"[主] face_result 执行异常: {e}")

    def _dispatch_http_command(self, payload):
        """执行电脑端下发的 HTTP 命令 {cmd, ...}"""
        cmd = payload.get("cmd") if isinstance(payload, dict) else None
        print("[主] HTTP 命令: " + str(payload))
        if cmd == "rtsp":
            self._set_rtsp(bool(payload.get("on", True)))
        elif cmd == "speak":
            # K230 无本机 TTS；语音播报走外接串口语音模块（无通用文本播报码），
            # 此处仅 LCD 提示 + LED/蜂鸣器反馈。如需文本播报，由电脑端 TTS 播放。
            self._set_toast("播报: " + str(payload.get("text", "")))
            self._led.flash(color=config.LED_COLOR_SUCCESS, times=1, interval_ms=60)
        elif cmd == "play_wav":
            self._set_toast("play_wav: " + str(payload.get("file", "")))
        elif cmd == "led":
            color = payload.get("color", [0, 0, 0])
            try:
                self._led.on((int(color[0]), int(color[1]), int(color[2])))
            except Exception:
                self._led.on(config.LED_COLOR_OFF)
        elif cmd == "exit":
            print("[主] HTTP exit 命令，退出程序")
            self._exit_flag = True
        else:
            print("[主] 未知 HTTP 命令: " + str(cmd))

    def _dispatch_face_result(self, payload):
        """消费电脑端推送的人脸识别结果，触发串口语音模块播报"""
        known = bool(payload.get("known", False)) if isinstance(payload, dict) else False
        label = payload.get("label", "") if isinstance(payload, dict) else ""
        print("[主] face_result: label=" + str(label) + " known=" + str(known))
        # 串口发码给语音模块：熟人 0x20 / 陌生人 0x21
        if self._serial:
            try:
                self._serial.send_face_result(known)
            except Exception as e:
                print(f"[主] 串口播报发送失败: {e}")
        # LED 反馈：熟人白闪 / 陌生人红闪
        self._led.flash(
            color=config.LED_COLOR_SUCCESS if known else config.LED_COLOR_UNKNOWN,
            times=1, interval_ms=80)

    def _set_rtsp(self, on):
        """按目标状态开/关 RTSP 推流（开推流必须在 RECOGNIZING 态，否则 sensor 流转停卡死）"""
        if self._rtsp is None:
            self._set_toast("RTSP 未初始化")
            return
        if not self._ip:
            self._set_toast("无 IP，请先连 WiFi")
            return
        if on:
            if not self._sm.is_state(int(config.State.RECOGNIZING)):
                self._sm.transition(int(config.State.RECOGNIZING))
            if not self._rtsp.is_running():
                self._rtsp.start()
            self._set_toast("RTSP开 " + self._rtsp.get_url())
        else:
            if self._rtsp.is_running():
                self._rtsp.stop()
            self._set_toast("RTSP 已关闭")

    # ---------- 按键回调 ----------
    def _on_short_press(self):
        """短按：IDLE->识别；录入态录入；识别态切换 RTSP 推流"""
        if self._sm.is_state(int(config.State.IDLE)):
            self._sm.transition(int(config.State.RECOGNIZING))
        elif self._sm.is_state(int(config.State.ENROLLING)):
            self._do_enroll()
        elif self._sm.is_state(int(config.State.RECOGNIZING)):
            self._toggle_rtsp()

    def _toggle_rtsp(self):
        """识别态短按：开/关 RTSP 推流"""
        if self._rtsp is None:
            self._set_toast("RTSP 未初始化")
            return
        if not self._ip:
            self._set_toast("无 IP，请先连 WiFi")
            return
        running = self._rtsp.toggle()
        if running:
            self._set_toast(f"RTSP开 {self._rtsp.get_url()}")
        else:
            self._set_toast("RTSP 已关闭")

    def _on_long_press(self):
        """长按：IDLE->识别 / 识别<->录入"""
        if self._sm.is_state(int(config.State.IDLE)):
            self._sm.transition(int(config.State.RECOGNIZING))
        elif self._sm.is_state(int(config.State.RECOGNIZING)):
            self._sm.transition(int(config.State.ENROLLING))
        elif self._sm.is_state(int(config.State.ENROLLING)):
            self._sm.transition(int(config.State.RECOGNIZING))

    def _on_exit_press(self):
        """超长按：退出"""
        print("[主] 超长按，退出程序")
        self._exit_flag = True

    # ---------- 串口命令回调 ----------
    def _on_command(self, cmd_id, data=b''):
        """串口语音命令回调（接收线程）：仅入队，由主循环消费"""
        with self._cmd_lock:
            self._cmd_queue.append(cmd_id)
        print("[主] 串口命令入队: " + hex(cmd_id))

    def _process_commands(self):
        """主循环调用：取出并执行队列里的串口命令（主线程安全）"""
        while True:
            with self._cmd_lock:
                if not self._cmd_queue:
                    return
                cmd_id = self._cmd_queue.pop(0)
            try:
                self._dispatch_command(cmd_id)
            except Exception as e:
                print(f"[主] 串口命令执行异常: {e}")

    def _dispatch_command(self, cmd_id):
        """执行串口命令"""
        from serial_voice import RecvCmd
        print("[主] 执行串口命令: " + hex(cmd_id))
        if cmd_id in (RecvCmd.HOME, RecvCmd.STOP):
            self._sm.transition(int(config.State.IDLE))
        elif cmd_id == RecvCmd.RECOGNIZE:
            self._sm.transition(int(config.State.RECOGNIZING))
        elif cmd_id == RecvCmd.ENROLL:
            self._sm.transition(int(config.State.ENROLLING))
        elif cmd_id == RecvCmd.RTSP_ON:
            if not self._sm.is_state(int(config.State.RECOGNIZING)):
                self._sm.transition(int(config.State.RECOGNIZING))
            if self._rtsp and self._ip and not self._rtsp.is_running():
                self._rtsp.toggle()
        elif cmd_id == RecvCmd.RTSP_OFF:
            if self._rtsp and self._rtsp.is_running():
                self._rtsp.toggle()
        elif cmd_id == RecvCmd.RTSP_TOGGLE:
            # 推流必须在 RECOGNIZING 态：需 get_frame+KPU 消费 CHN2 推进 sensor 流转，
            # 否则 IDLE 态 CHN2 不消费，sensor 流转停 → RTSP 卡死 + CHN2 snapshot failed
            if not self._sm.is_state(int(config.State.RECOGNIZING)):
                self._sm.transition(int(config.State.RECOGNIZING))
            self._toggle_rtsp()
        elif cmd_id == RecvCmd.ENROLL_CAPTURE:
            if not self._sm.is_state(int(config.State.ENROLLING)):
                self._sm.transition(int(config.State.ENROLLING))
            self._do_enroll()

    def _send_state(self, state_cmd):
        """发状态播报给语音模块"""
        if self._serial and state_cmd is not None:
            try:
                self._serial.send_state(state_cmd)
            except Exception:
                pass

    # ---------- 状态进/出回调 ----------
    def _on_enter_idle(self):
        print("[主] 进入待机态，等待串口语音命令")
        self._led.set_state(int(config.State.IDLE))
        self._set_toast("待机: 等串口语音命令")
        if self._send_cmd:
            self._send_state(self._send_cmd.STATE_IDLE)
        if self._touch:
            from serial_voice import RecvCmd
            self._touch.set_active(RecvCmd.HOME)

    def _on_exit_idle(self):
        pass

    def _on_enter_recognizing(self):
        print("[主] 进入识别态")
        self._led.set_state(int(config.State.RECOGNIZING))
        if self._send_cmd:
            self._send_state(self._send_cmd.STATE_RECOGNIZING)
        if self._touch:
            from serial_voice import RecvCmd
            self._touch.set_active(RecvCmd.RECOGNIZE)

    def _on_exit_recognizing(self):
        pass

    def _on_enter_enrolling(self):
        print("[主] 进入录入态，短按录入")
        self._led.set_state(int(config.State.ENROLLING))
        self._set_toast("录入态: 短按/串口录入 长按返回")
        if self._send_cmd:
            self._send_state(self._send_cmd.STATE_ENROLLING)
        if self._touch:
            from serial_voice import RecvCmd
            self._touch.set_active(RecvCmd.ENROLL)

    def _on_exit_enrolling(self):
        self._face.cancel_enrollment()

    # ---------- 录入 ----------
    def _do_enroll(self):
        label = "user_{}".format(self._face.get_class_count() + 1)
        if not self._face.start_enrollment(label):
            return
        try:
            img_np = self._pl.get_frame()
            success, msg, count = self._face.enroll_face(img_np)
            if success:
                self._led.flash(color=config.LED_COLOR_SUCCESS, times=2, interval_ms=80)
                self._set_toast(f"录入成功: {label} (共{count})")
            else:
                self._set_toast(f"录入失败: {msg}")
        except Exception as e:
            self._set_toast(f"录入异常: {e}")
            self._face.cancel_enrollment()

    def _set_toast(self, msg):
        """设置浮层提示信息（显示一段时间后消失）"""
        self._toast_msg = msg
        self._toast_time = time.ticks_ms()

    # ---------- 状态 handler ----------
    def _handle_idle(self):
        """待机态：不取 AI 帧、不跑 KPU，只显示待机提示（最省资源）"""
        osd_img = self._pl.osd_img
        osd_img.clear()
        osd_img.draw_string_advanced(10, 10, 32, "待机:等串口语音命令",
                                     color=config.TEXT_COLOR_WHITE)
        osd_img.draw_string_advanced(10, 56, 20, "串口命令: 主页/识别/录入/停止",
                                     color=config.TEXT_COLOR_YELLOW)
        osd_img.draw_string_advanced(10, 90, 18, "长按切录入 / 超长按退出",
                                     color=config.TEXT_COLOR_BLUE)
        # 状态栏：IP / 已录入数
        try:
            n = self._face.get_class_count()
            ds = self._pl.get_display_size()
            osd_img.draw_string_advanced(ds[0] - 200, 10, 22,
                                         "已录入:" + str(n), color=config.TEXT_COLOR_WHITE)
            ip_str = self._ip if self._ip else "无IP"
            osd_img.draw_string_advanced(10, 120, 18, "IP:" + ip_str,
                                         color=config.TEXT_COLOR_BLUE)
        except Exception:
            pass
        # 浮层提示
        if self._toast_msg and time.ticks_diff(time.ticks_ms(), self._toast_time) < config.ENROLL_SHOW_TIME:
            osd_img.draw_string_advanced(10, 150, 26, self._toast_msg,
                                         color=config.TEXT_COLOR_YELLOW)

    def _handle_recognizing(self):
        """识别态：每帧轻量检测画框（跟脸紧），节流做完整识别拿 label，按 IoU 合并"""
        img_np = self._pl.get_frame()
        now = time.ticks_ms()
        # 每帧轻量检测（仅位置，不提特征）-> 框跟得紧
        try:
            cur_faces = self._face.detect_faces_only(img_np)
        except Exception as e:
            print(f"[主] 轻量检测异常: {e}")
            cur_faces = []
        # 节流做完整识别（提特征+匹配）-> 拿 label
        if (time.ticks_diff(now, self._last_recog_time) >= config.RECOGNIZE_DETECT_INTERVAL_MS
                or not self._last_labeled):
            try:
                self._last_labeled = self._face.detect_and_recognize(img_np)
            except Exception as e:
                print(f"[主] 识别异常: {e}")
                self._last_labeled = []
            self._last_recog_time = now
        # 合并：cur_faces 的位置 + last_labeled 的 label
        faces = self._merge_faces(cur_faces, self._last_labeled)
        self._last_faces = faces
        # 绘制
        self._draw_faces(self._pl.osd_img, faces, known_color=config.FACE_BOX_COLOR_KNOWN,
                         unknown_color=config.FACE_BOX_COLOR_UNKNOWN)
        self._draw_status(self._pl.osd_img, "识别中")

    @staticmethod
    def _iou(a, b):
        ax2, ay2 = a['x'] + a['w'], a['y'] + a['h']
        bx2, by2 = b['x'] + b['w'], b['y'] + b['h']
        ix1, iy1 = max(a['x'], b['x']), max(a['y'], b['y'])
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
        inter = iw * ih
        union = a['w'] * a['h'] + b['w'] * b['h'] - inter
        return inter / union if union > 0 else 0.0

    def _merge_faces(self, cur, labeled):
        """cur 的位置 + labeled 的 label（按最大 IoU 匹配）"""
        if not labeled:
            for f in cur:
                f['label'] = 'unknown'
                f['class_id'] = 0
                f['score'] = 0.0
            return cur
        labels = self._face.get_labels()
        for cf in cur:
            best_label = 'unknown'
            best_iou = 0.0
            for lf in labeled:
                iou = self._iou(cf, lf)
                if iou > best_iou:
                    best_iou = iou
                    best_label = lf.get('label', 'unknown')
            cf['label'] = best_label
            cf['class_id'] = labels.index(best_label) if best_label in labels else 0
            cf['score'] = lf.get('score', 0.0) if best_iou > 0.1 else 0.0
        return cur

    def _handle_enrolling(self):
        """录入态：取帧 -> 仅检测 -> 画框 -> 提示"""
        img_np = self._pl.get_frame()
        try:
            faces = self._face.detect_faces_only(img_np)
        except Exception as e:
            print(f"[主] 录入态检测异常: {e}")
            faces = []
        self._draw_faces(self._pl.osd_img, faces, known_color=config.FACE_BOX_COLOR_DETECTED,
                         unknown_color=config.FACE_BOX_COLOR_DETECTED, show_label=False)
        self._draw_status(self._pl.osd_img, "录入中: 短按录入")

    # ---------- 绘制 ----------
    def _draw_faces(self, osd_img, faces, known_color, unknown_color, show_label=True):
        # 检测坐标在 rgb888p 系，OSD 在 display 系，需按比例缩放
        try:
            ds = self._pl.get_display_size()
            sx = ds[0] / config.RGB888P_WIDTH
            sy = ds[1] / config.RGB888P_HEIGHT
        except Exception:
            sx = sy = 1.0
        for f in faces:
            try:
                x = int(f['x'] * sx)
                y = int(f['y'] * sy)
                w = int(f['w'] * sx)
                h = int(f['h'] * sy)
                label = f.get('label', 'unknown')
                is_known = f.get('class_id', 0) > 0
                color = known_color if is_known else unknown_color
                osd_img.draw_rectangle(x, y, w, h, color=color, thickness=2)
                # 关键点
                kp = f.get('points')
                if kp:
                    for (px, py) in kp:
                        osd_img.draw_cross(int(px * sx), int(py * sy), color=color, thickness=2)
                if show_label:
                    txt = f"{label} {f.get('score', 0):.2f}" if is_known else "unknown"
                    osd_img.draw_string_advanced(x, max(0, y - 30), 24, txt, color=color)
            except Exception as e:
                print(f"[主] 绘制人脸异常: {e}")

    def _draw_status(self, osd_img, status):
        try:
            # 顶部状态栏
            osd_img.draw_string_advanced(10, 10, 28, status, color=config.TEXT_COLOR_WHITE)
            n = self._face.get_class_count()
            osd_img.draw_string_advanced(self._pl.get_display_size()[0] - 200, 10, 24,
                                         f"已录入:{n}", color=config.TEXT_COLOR_WHITE)
            # IP
            ip_str = self._ip if self._ip else "无IP"
            osd_img.draw_string_advanced(10, 42, 20, f"IP:{ip_str}", color=config.TEXT_COLOR_BLUE)
            # RTSP 状态
            if self._rtsp is not None and self._rtsp.is_running():
                osd_img.draw_string_advanced(10, 64, 20, self._rtsp.get_url(),
                                             color=config.TEXT_COLOR_GREEN)
            # 浮层提示（2 秒内显示）
            if self._toast_msg and time.ticks_diff(time.ticks_ms(), self._toast_time) < config.ENROLL_SHOW_TIME:
                osd_img.draw_string_advanced(10, 90, 28, self._toast_msg,
                                             color=config.TEXT_COLOR_YELLOW)
        except Exception:
            pass

    # ---------- 主循环 ----------
    def run(self):
        print("[主] 初始化...")
        # 1. WiFi 联网
        self._connect_wifi()
        # 2. 管线（内部创建 sensor 并配置）
        self._init_pipeline()
        # 3. RTSP 管理器（复用 sensor 绑 VENC 直推）
        self._rtsp = RtspManager(sensor=self._sensor, ip=self._ip)
        # 4. 业务模块
        self._init_modules()
        # 5. 状态机
        self._register_states()
        # 6. HTTP 命令服务（电脑端联机接口）
        self._init_http()
        # 触发进入待机态回调（设置 LED；默认 IDLE 不经 transition，故手动触发）
        self._on_enter_idle()
        print("[主] 进入主循环")

        try:
            while True:
                os.exitpoint()
                if self._exit_flag:
                    break
                self._frame_count += 1

                # 清空 OSD
                self._pl.osd_img.clear()

                # 按键
                self._key.update()
                # LED
                self._led.update()
                # 串口命令（主线程消费队列）
                self._process_commands()
                # HTTP 命令 + face_result（主线程消费队列，电脑端联机）
                self._process_http_commands()
                self._process_face_results()
                # 触摸屏（读触点，命中入队）
                if self._touch:
                    self._touch.update()
                # 状态机业务（绘制到 osd_img）
                self._sm.update()
                # 状态上报给 HTTP 服务（跳帧降开销，指纹变化才重建 JSON）
                if self._frame_count % config.STATUS_SKIP_FRAMES == 0:
                    self._update_http_status()
                # 触摸侧边栏画在最上层
                if self._touch:
                    self._touch.draw(self._pl.osd_img)

                # 显示
                self._pl.show_image()
                if self._frame_count % 15 == 0:
                    gc.collect()
                # 诊断：每 300 帧打印空闲堆内存，定位长跑卡死是否内存泄漏
                if self._frame_count % 300 == 0:
                    try:
                        print("[主] mem_free=" + str(gc.mem_free()))
                    except Exception:
                        pass
        except KeyboardInterrupt:
            print("[主] 用户中断")
        except BaseException as e:
            print(f"[主] 异常: {e}")
        finally:
            self._cleanup()

    def _cleanup(self):
        print("[主] 清理资源")
        try:
            if self._http:
                self._http.destroy()
        except Exception:
            pass
        try:
            if self._rtsp:
                self._rtsp.stop()
        except Exception:
            pass
        try:
            if self._serial:
                self._serial.destroy()
        except Exception:
            pass
        try:
            if self._face:
                self._face.deinit()
        except Exception:
            pass
        try:
            if self._led:
                self._led.destroy()
        except Exception:
            pass
        try:
            if self._pl:
                self._pl.destroy()
        except Exception:
            pass
        try:
            disconnect_wifi(self._wlan)
        except Exception:
            pass
        try:
            os.exitpoint(os.EXITPOINT_ENABLE_SLEEP)
            time.sleep_ms(100)
            MediaManager.deinit()
        except Exception:
            pass


def main():
    app = FaceRecognitionApp()
    app.run()


if __name__ == "__main__":
    main()
