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
from buzzer_controller import BuzzerController
from face_detector import FaceDetector
from wifi_manager import connect_wifi, disconnect_wifi
from rtsp_manager import RtspManager
from http_cmd_server import HttpCmdServer
from voice_manager import VoiceManager


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
        self._buzzer = None
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

        # 在线语音识别（DashScope 一次性录音，按钮触发）
        self._ovr = None
        self._voice_cmd_queue = []
        self._voice_cmd_lock = _thread.allocate_lock()
        self._rtsp_resume_after_voice = False  # 录音前暂停了 RTSP，录完需恢复
        self._voice_text = ""        # 最近一次识别到的文本（屏幕显示用）
        self._voice_text_time = 0    # 文本产生时刻（控制显示时长）

        # 触摸屏 UI
        self._touch = None

        # 离线语音管理器（触摸"语音"按钮触发，kws.kmodel / speech_recognizer）
        self._voice = None
        self._voice_wake_queue = []

        # 运行时状态
        self._exit_flag = False
        self._last_faces = []          # 缓存上次识别结果（节流用）
        self._last_labeled = []        # 上次完整识别（带 label）的结果
        self._last_recog_time = 0      # 上次完整识别时间
        self._last_face_present = False  # 蜂鸣器去抖：上帧是否有人脸（边沿触发用）
        self._last_alarm_time = 0        # 蜂鸣器去抖：上次鸣响时间
        self._toast_msg = ""           # 浮层提示信息（录入/RTSP 等）
        self._toast_time = 0
        self._frame_count = 0

        # WiFi 设置界面状态
        self._wifi_mode = False        # True: 在 WiFi 设置全屏界面
        self._wifi_ui = None           # WiFiUI 实例（_init_modules 中创建）

        # 阈值设置界面状态
        self._threshold_mode = False   # True: 在 阈值设置全屏界面
        self._threshold_ui = None      # ThresholdUI 实例（_init_modules 中创建）

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
        # 优先用保存的 WiFi 配置（最近一次成功连接的）
        ssid = config.WIFI_SSID
        pwd = config.WIFI_PASSWORD
        try:
            from wifi_ui import load_saved_wifi
            saved = load_saved_wifi()
            if saved:
                ssid, pwd = saved
                print("[主] 用保存的配置: " + ssid)
            else:
                print("[主] 用默认配置: " + ssid)
        except Exception as e:
            print("[主] 读保存配置异常，用默认: " + str(e))
        # 离线开机时 WiFi 驱动可能未就绪，重试 3 次
        ip = None
        wlan = None
        for attempt in range(3):
            ip, wlan = connect_wifi(ssid, pwd)
            if ip:
                break
            print("[主] WiFi 第 " + str(attempt + 1) + " 次连接失败，3秒后重试...")
            if attempt < 2:
                time.sleep(3)
        self._ip = ip
        self._wlan = wlan
        if ip:
            self._set_toast(f"WiFi已连 IP:{ip}")
            if self._buzzer:
                try:
                    self._buzzer.beep_wifi()
                except Exception:
                    pass
        else:
            self._set_toast("WiFi连接失败，可在WiFi界面手动设置")

    def _init_modules(self):
        print("[主] 初始化 FaceDetector...")
        self._face = FaceDetector(
            faces_db_path=config.FACES_DB_DIR,
            conf_th=config.FACE_CONF_THRESHOLD,
            iou_th=config.FACE_IOU_THRESHOLD,
            recognize_th=config.FACE_RECOGNIZE_THRESHOLD,
            use_alignment=True,
        )
        # 持久化阈值覆盖：读 /data/threshold.json 覆盖 config 默认值
        try:
            from threshold_ui import load_saved_threshold
            saved = load_saved_threshold()
            if saved:
                rc = saved.get("recognize")
                cf = saved.get("conf")
                io = saved.get("iou")
                if rc is not None or cf is not None or io is not None:
                    self._face.set_detect_threshold(conf_th=cf, iou_th=io, recognize_th=rc)
                    print("[主] 已加载保存的阈值: " + str(saved))
        except Exception as e:
            print(f"[主] 加载阈值失败（用默认）: {e}")
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
        print("[主] 初始化 WiFiUI...")
        from wifi_ui import WiFiUI
        self._wifi_ui = WiFiUI(self)
        print("[主] WiFiUI 完成")
        print("[主] 初始化 ThresholdUI...")
        from threshold_ui import ThresholdUI
        self._threshold_ui = ThresholdUI(self)
        print("[主] ThresholdUI 完成")

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


    def _init_online_voice(self):
        """启动在线语音识别（DashScope 流式），默认暂停，按钮触发才开始监听"""
        if not config.ONLINE_VOICE_ENABLE:
            print("[主] 在线语音未启用 (ONLINE_VOICE_ENABLE=False)")
            return
        if not self._ip:
            print("[主] 无 IP，跳过在线语音初始化")
            return
        try:
            from online_voice_recognition import OnlineVoiceRecognition
            self._ovr = OnlineVoiceRecognition(
                api_key=config.DASHSCOPE_API_KEY,
                sample_rate=config.ONLINE_VOICE_SAMPLE_RATE,
                chunk_ms=getattr(config, "ONLINE_VOICE_CHUNK_MS", 100),
                record_ms=getattr(config, "ONLINE_VOICE_RECORD_MS", 4000),
            )
            self._ovr.start(
                keywords=config.ONLINE_VOICE_KEYWORDS,
                callback=self._on_voice_command,
            )
            # 默认不监听，等按钮触发
            print("[主] 在线语音已就绪（按语音按钮录 4s 识别）")
        except Exception as e:
            print(f"[主] 在线语音初始化失败（不影响按键/识别）: {e}")
            self._ovr = None

    def _on_voice_command(self, command):
        """在线语音命中回调（会话线程）：仅入队，主循环执行"""
        with self._voice_cmd_lock:
            self._voice_cmd_queue.append(command)
        print("[主] 在线语音命令入队: " + str(command))

    def _process_voice_commands(self):
        """主循环调用：消费在线语音命令队列"""
        while True:
            with self._voice_cmd_lock:
                if not self._voice_cmd_queue:
                    return
                command = self._voice_cmd_queue.pop(0)
            try:
                self._set_toast("语音: " + str(command))
                self._dispatch_http_command({"cmd": command, "source": "voice"})
            except Exception as e:
                print(f"[主] 在线语音命令执行异常: {e}")

    def _process_voice_finish(self):
        """主循环调用：一次录音会话结束后处理（恢复推流/提示未命中）。
        若命中了关键词（已切界面），不强行恢复 RTSP，尊重用户语音意图。"""
        if self._ovr is None:
            return
        if not self._ovr.poll_finished():
            return
        matched = self._ovr.last_matched()
        # 捕获识别文本供屏幕显示（无论是否命中关键词）
        try:
            txt = self._ovr.last_text() or ""
        except Exception:
            txt = ""
        if txt:
            self._voice_text = txt
            self._voice_text_time = time.ticks_ms()
        if matched:
            # 命中关键词：命令已由 _process_voice_commands 执行，提示即可
            self._buzz("beep_success")
        else:
            self._set_toast("语音: 未识别到关键词")
        # 录音前若暂停过推流，且本次未切界面，则恢复推流
        if self._rtsp_resume_after_voice:
            self._rtsp_resume_after_voice = False
            if not matched:
                try:
                    if not self._sm.is_state(int(config.State.RECOGNIZING)):
                        self._sm.transition(int(config.State.RECOGNIZING))
                    if self._rtsp and not self._rtsp.is_running():
                        self._rtsp.start()
                        self._set_toast("语音结束，已恢复推流")
                except Exception as e:
                    print(f"[主] 恢复推流失败: {e}")

    def _init_voice(self):
        """初始化离线语音管理器（懒加载：开机不加载 kws.kmodel 占 KPU，按语音按钮时才加载）"""
        if not config.VOICE_MGR_ENABLE:
            print("[主] 离线语音未启用 (VOICE_MGR_ENABLE=False)")
            return
        if self._ovr is not None:
            print("[主] 在线语音已启用，跳过离线语音（麦克风资源互斥）")
            return
        if self._voice is not None:
            return   # 已初始化，不重复
        try:
            self._voice = VoiceManager()
            ok = self._voice.start(
                on_command=self._on_command,
                on_wake=self._on_voice_wake,
            )
            if ok:
                mode = self._voice.get_mode()
                print(f"[主] 离线语音已就绪（方案: {mode}）")
                self._set_toast(f"语音就绪({mode})")
            else:
                print("[主] 离线语音初始化失败")
                self._voice = None
        except Exception as e:
            print(f"[主] 离线语音初始化异常: {e}")
            self._voice = None

    def _on_voice_wake(self):
        """kws 唤醒词回调（KWS 线程）：入队，主循环消费切态"""
        with self._cmd_lock:
            self._voice_wake_queue.append(True)
        print("[主] 语音唤醒入队")

    def _process_voice_wake(self):
        """主循环调用：消费唤醒队列，按当前状态循环切换"""
        while self._voice_wake_queue:
            self._voice_wake_queue.pop(0)
            try:
                self._cycle_state_on_wake()
            except Exception as e:
                print(f"[主] 语音唤醒切态异常: {e}")

    def _cycle_state_on_wake(self):
        """唤醒词触发：按当前状态循环切换 待机→识别→录入→待机"""
        if self._sm.is_state(int(config.State.IDLE)):
            self._sm.transition(int(config.State.RECOGNIZING))
            self._set_toast("语音: 待机→识别")
        elif self._sm.is_state(int(config.State.RECOGNIZING)):
            self._sm.transition(int(config.State.ENROLLING))
            self._set_toast("语音: 识别→录入")
        elif self._sm.is_state(int(config.State.ENROLLING)):
            self._sm.transition(int(config.State.IDLE))
            self._set_toast("语音: 录入→待机")
        # 停止本次监听（已切态，等下次按钮触发）
        if self._voice:
            self._voice.stop_listening()
        self._buzz_transition()

    def _update_http_status(self):
        """主循环调用：把当前状态上报给 HTTP 服务（指纹变化才重建 JSON）"""
        if self._http is None:
            return
        try:
            state_name = config.STATE_NAMES.get(self._sm.state, "空闲")
            rtsp_running = self._rtsp is not None and self._rtsp.is_running()
            rtsp_url = self._rtsp.get_url() if self._rtsp else ""
            # 人脸预警：仅识别态读 _last_labeled；非识别态强制清零，避免残留旧值误报
            face_count = 0
            known_count = 0
            unknown_count = 0
            face_labels = []
            alarm = False
            if self._sm.is_state(int(config.State.RECOGNIZING)):
                faces = self._last_labeled or []
                face_count = len(faces)
                for f in faces:
                    label = f.get("label", "unknown")
                    if label and label != "unknown":
                        known_count += 1
                        face_labels.append(label)
                    else:
                        unknown_count += 1
                alarm = unknown_count > 0
            self._http.update_status({
                "ip": self._ip or "",
                "rtsp_url": rtsp_url,
                "rtsp_running": rtsp_running,
                "state": state_name,
                "audio_busy": False,
                "face_count": face_count,
                "known_count": known_count,
                "unknown_count": unknown_count,
                "face_labels": face_labels,
                "alarm": alarm,
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
        elif cmd == "home" or cmd == "stop":
            # 回主页/停止：切待机态（与串口 HOME/STOP 同语义）
            self._sm.transition(int(config.State.IDLE))
        elif cmd == "recognize":
            self._sm.transition(int(config.State.RECOGNIZING))
        elif cmd == "enroll":
            self._sm.transition(int(config.State.ENROLLING))
        elif cmd == "rtsp_on":
            self._set_rtsp(True)
        elif cmd == "rtsp_off":
            self._set_rtsp(False)
        elif cmd == "enroll_capture":
            if not self._sm.is_state(int(config.State.ENROLLING)):
                self._sm.transition(int(config.State.ENROLLING))
            self._do_enroll()
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
        # 蜂鸣器反馈：熟人成功音 / 陌生人报警音
        self._buzz("beep_success" if known else "beep_alarm")

    def _set_rtsp(self, on):
        """按目标状态开/关 RTSP 推流（仅控制推流，不改变当前状态机状态）"""
        if self._rtsp is None:
            self._set_toast("RTSP 未初始化")
            return
        if not self._ip:
            self._set_toast("无 IP，请先连 WiFi")
            return
        if on:
            # 推流前彻底释放语音资源（KPU + 麦克风，与 VENC 互斥）
            if self._voice:
                self._voice.destroy()
                self._voice = None
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
        # 推流前彻底释放语音资源（kws.kmodel 占 KPU + PyAudio 占麦克风，与 VENC 冲突）
        if not self._rtsp.is_running() and self._voice:
            self._voice.destroy()
            self._voice = None
            print("[主] 推流前释放语音资源")
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
            # 仅开推流，不切状态机
            if self._rtsp and self._ip and not self._rtsp.is_running():
                self._rtsp.toggle()
        elif cmd_id == RecvCmd.RTSP_OFF:
            if self._rtsp and self._rtsp.is_running():
                self._rtsp.toggle()
        elif cmd_id == RecvCmd.RTSP_TOGGLE:
            # 仅切换推流，不切状态机（推流只依赖 sensor CHN0->VENC，与识别态无关）
            self._toggle_rtsp()
        elif cmd_id == RecvCmd.ENROLL_CAPTURE:
            if not self._sm.is_state(int(config.State.ENROLLING)):
                self._sm.transition(int(config.State.ENROLLING))
            self._do_enroll()
        elif cmd_id == RecvCmd.WIFI_SETTINGS:
            # 切换 WiFi 设置界面（toggle）
            self._wifi_mode = not self._wifi_mode
            if self._wifi_mode:
                self._set_toast("进入 WiFi 设置")
                if self._wifi_ui:
                    self._wifi_ui.enter()
            else:
                self._set_toast("退出 WiFi 设置")
                if self._wifi_ui:
                    self._wifi_ui.exit()
            self._buzz_transition()  # 切界面提示音
        elif cmd_id == RecvCmd.THRESHOLD_SETTINGS:
            # 触摸"阈值"按钮：进入/退出 阈值设置界面
            self._threshold_mode = not self._threshold_mode
            if self._threshold_mode:
                self._set_toast("进入阈值设置")
                if self._threshold_ui:
                    self._threshold_ui.enter()
            else:
                self._set_toast("退出阈值设置")
                if self._threshold_ui:
                    self._threshold_ui.exit()
            self._buzz_transition()
        elif cmd_id == RecvCmd.VOICE_START:
            # 触摸"语音"按钮：一次性录音识别（录 4s → 上传 → 回发切界面）
            if self._ovr is None:
                self._set_toast("在线语音未就绪（检查 WiFi）")
            elif self._ovr.is_listening():
                # 会话进行中再按一次 = 取消
                self._ovr.stop_listening()
                self._set_toast("语音: 已取消")
            else:
                # 录音(麦克风)与 RTSP(VENC) 互斥：录音前先暂停推流，录完自动恢复
                if self._rtsp and self._rtsp.is_running():
                    self._rtsp.stop()
                    self._rtsp_resume_after_voice = True
                    self._set_toast("暂停推流，请说话 4s...")
                else:
                    self._rtsp_resume_after_voice = False
                    self._set_toast("请说话 4s...")
                rec_ms = getattr(config, "ONLINE_VOICE_RECORD_MS", 4000)
                self._ovr.start_listening(timeout_ms=rec_ms)
                self._buzz_transition()

    def _send_state(self, state_cmd):
        """发状态播报给语音模块"""
        if self._serial and state_cmd is not None:
            try:
                self._serial.send_state(state_cmd)
            except Exception:
                pass

    # ---------- 状态进/出回调 ----------
    def _on_enter_idle(self):
        print("[主] 进入待机态")
        self._led.set_state(int(config.State.IDLE))
        self._buzz_transition()
        self._set_toast("待机: 触摸/按键/语音切换")
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
        self._buzz_transition()
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
        self._buzz_transition()
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
                self._buzz("beep_success")  # 录入成功提示音
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

    # ---------- 蜂鸣器反馈 ----------
    def _buzz(self, method_name):
        """安全调用蜂鸣器方法（初始化前/禁用时不报错）"""
        if self._buzzer is None:
            return
        try:
            getattr(self._buzzer, method_name)()
        except Exception as e:
            print(f"[主] 蜂鸣器异常: {e}")

    def _buzz_transition(self):
        """切换界面/状态：短促提示音"""
        self._buzz("beep_transition")

    def _trigger_face_buzzer(self, faces):
        """识别到人脸时边沿触发鸣响（去抖）：陌生脸报警 / 熟人成功音。
        仅在“无人脸->有人脸”边沿触发，避免每帧响、与人脸识别抢 GIL。"""
        if self._buzzer is None:
            return
        present = len(faces) > 0
        if present and not self._last_face_present:
            now = time.ticks_ms()
            if time.ticks_diff(now, self._last_alarm_time) >= config.TRIGGER_DEBOUNCE_MS:
                has_unknown = any(f.get('class_id', 0) == 0 for f in faces)
                self._buzz("beep_alarm" if has_unknown else "beep_success")
                self._last_alarm_time = now
        self._last_face_present = present

    # ---------- WiFi 设置界面（委托给 WiFiUI） ----------
    def _handle_wifi_settings(self):
        """全屏 OSD 绘制 + 触摸处理，全部委托给 WiFiUI 实例"""
        if self._wifi_ui is None:
            self._wifi_mode = False
            return
        # 绘制全屏 OSD
        self._wifi_ui.draw(self._pl.osd_img)
        # 触摸处理：复用 TouchUI 的去抖状态（DOWN 上升沿 + 300ms 去抖）
        if self._touch is None:
            return
        try:
            pts = self._touch._tp.read(1)
            if not pts:
                self._touch._last_evt = None
                return
            pt = pts[0]
            down_evt = self._touch._down_evt
            is_down = (pt.event == down_evt)
            last_evt = self._touch._last_evt
            is_down_edge = (is_down and last_evt != down_evt)
            if is_down_edge:
                now = time.ticks_ms()
                if time.ticks_diff(now, self._touch._last_trigger) >= 300:
                    self._touch._last_trigger = now
                    result = self._wifi_ui.handle_touch(pt, is_down_edge=True)
                    if result == "exit":
                        # WiFiUI 通知退出
                        self._wifi_mode = False
                        self._wifi_ui.exit()
                        self._set_toast("退出 WiFi 设置")
                        self._buzz_transition()  # 切界面提示音
            self._touch._last_evt = pt.event
        except Exception as e:
            print("[主] WiFi 触摸处理异常: " + str(e))

    # ---------- 阈值设置界面（委托给 ThresholdUI） ----------
    def _handle_threshold_settings(self):
        """全屏 OSD 绘制 + 触摸处理，全部委托给 ThresholdUI 实例"""
        if self._threshold_ui is None:
            self._threshold_mode = False
            return
        # 绘制全屏 OSD
        self._threshold_ui.draw(self._pl.osd_img)
        # 触摸处理：复用 TouchUI 的去抖状态（DOWN 上升沿 + 300ms 去抖）
        if self._touch is None:
            return
        try:
            pts = self._touch._tp.read(1)
            if not pts:
                self._touch._last_evt = None
                return
            pt = pts[0]
            down_evt = self._touch._down_evt
            is_down = (pt.event == down_evt)
            last_evt = self._touch._last_evt
            is_down_edge = (is_down and last_evt != down_evt)
            if is_down_edge:
                now = time.ticks_ms()
                if time.ticks_diff(now, self._touch._last_trigger) >= 300:
                    self._touch._last_trigger = now
                    result = self._threshold_ui.handle_touch(pt, is_down_edge=True)
                    if result == "exit":
                        # ThresholdUI 通知退出（已保存）
                        self._threshold_mode = False
                        self._threshold_ui.exit()
                        self._set_toast("阈值已保存")
                        self._buzz_transition()  # 切界面提示音
            self._touch._last_evt = pt.event
        except Exception as e:
            print("[主] 阈值触摸处理异常: " + str(e))

    # ---------- 状态 handler ----------
    def _handle_idle(self):
        """待机态：不取 AI 帧、不跑 KPU，只显示待机提示（最省资源）"""
        osd_img = self._pl.osd_img
        osd_img.clear()
        # 标题
        osd_img.draw_string_advanced(10, 10, 36, "待机",
                                     color=config.TEXT_COLOR_WHITE)
        # 状态信息
        try:
            n = self._face.get_class_count()
            ip_str = self._ip if self._ip else "无IP"
            osd_img.draw_string_advanced(10, 60, 20, "IP: " + ip_str,
                                         color=config.TEXT_COLOR_BLUE)
            osd_img.draw_string_advanced(10, 86, 20, "已录入: " + str(n) + " 人",
                                         color=config.TEXT_COLOR_WHITE)
            # RTSP 状态
            if self._rtsp and self._rtsp.is_running():
                osd_img.draw_string_advanced(10, 112, 18, self._rtsp.get_url(),
                                             color=config.TEXT_COLOR_GREEN)
        except Exception:
            pass
        # 操作提示
        osd_img.draw_string_advanced(10, 146, 16, "侧栏: 主页/识别/录入/推流/语音",
                                     color=config.TEXT_COLOR_YELLOW)
        osd_img.draw_string_advanced(10, 170, 16, "按键: 长按切录入 超长按退出",
                                     color=config.TEXT_COLOR_BLUE)
        # 浮层提示
        if self._toast_msg and time.ticks_diff(time.ticks_ms(), self._toast_time) < config.ENROLL_SHOW_TIME:
            osd_img.draw_string_advanced(10, 200, 26, self._toast_msg,
                                         color=config.TEXT_COLOR_YELLOW)
        # 语音识别屏幕提示（录音中/识别中/识别文本）
        self._draw_voice_status(self._pl.osd_img, 230)
        # 离线语音（kws）监听中提示
        if self._voice and self._voice.is_listening():
            osd_img.draw_string_advanced(10, 262, 22, "[MIC] 监听中...",
                color=(0, 255, 200))

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
        # 蜂鸣器：识别到人脸时边沿触发（陌生脸报警 / 熟人成功音，去抖）
        self._trigger_face_buzzer(faces)
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

    def _draw_voice_status(self, osd_img, y):
        """绘制在线语音识别的屏幕提示（录音中/识别中/识别结果文本）。
        y 为起始纵坐标。三态：录音倒计时、上传等待、识别到的文本。"""
        if self._ovr is None or not hasattr(self._ovr, 'is_listening'):
            return
        try:
            listening = self._ovr.is_listening()
            # ovr 阶段常量：PHASE_IDLE=0 / PHASE_RECORDING=1 / PHASE_UPLOADING=2
            phase = self._ovr.get_phase() if hasattr(self._ovr, 'get_phase') else 0
        except Exception:
            listening = False
            phase = 0

        # 1) 会话进行中：录音倒计时 或 上传等待
        if listening:
            if phase == 1:  # RECORDING
                remaining = self._ovr.get_remaining_ms() if hasattr(self._ovr, 'get_remaining_ms') else -1
                if remaining > 0:
                    sec = (remaining + 999) // 1000
                    osd_img.draw_string_advanced(10, y, 30,
                        f"请说话... {sec}s", color=(0, 255, 200))
                else:
                    osd_img.draw_string_advanced(10, y, 26, "请说话...",
                        color=(0, 255, 200))
            elif phase == 2:  # UPLOADING
                osd_img.draw_string_advanced(10, y, 24, "识别中，请稍候...",
                    color=(255, 200, 0))
                # 上传阶段若已有实时文本，紧接着显示
                try:
                    live = self._ovr.last_text() or ""
                except Exception:
                    live = ""
                if live:
                    osd_img.draw_string_advanced(10, y + 30, 22,
                        "识别: " + live, color=(0, 255, 200))
            return

        # 2) 会话已结束：在显示窗口内展示识别到的文本（5 秒）
        if self._voice_text and time.ticks_diff(time.ticks_ms(), self._voice_text_time) < 5000:
            osd_img.draw_string_advanced(10, y, 24,
                "识别: " + self._voice_text, color=(0, 255, 200))

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
            # 语音识别屏幕提示（录音中/识别中/识别文本）
            self._draw_voice_status(osd_img, 86)
            # 离线语音（kws）监听中提示
            if self._voice and self._voice.is_listening():
                osd_img.draw_string_advanced(10, 118, 22, "[MIC] 监听中...",
                                             color=(0, 255, 200))
            # 浮层提示（2 秒内显示；下移避开语音文本区）
            if self._toast_msg and time.ticks_diff(time.ticks_ms(), self._toast_time) < config.ENROLL_SHOW_TIME:
                osd_img.draw_string_advanced(10, 150, 28, self._toast_msg,
                                             color=config.TEXT_COLOR_YELLOW)
        except Exception:
            pass

    # ---------- 主循环 ----------
    def run(self):
        print("[主] 初始化...")
        # 0. 蜂鸣器（先于 WiFi，以便连上 WiFi 时能鸣响提示）
        self._buzzer = BuzzerController()
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
        # 7. 在线语音识别（DashScope 流式，按钮触发监听）
        self._init_online_voice()
        # 8. 离线语音：懒加载（不在开机时加载 kws.kmodel 占 KPU，按语音按钮时才初始化）
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
                # 离线语音：唤醒队列消费 + 超时自动停止
                self._process_voice_wake()
                if self._voice:
                    self._voice.check_timeout()
                # 在线语音：命令队列消费 + 录音结束检查（恢复 RTSP）
                self._process_voice_commands()
                self._process_voice_finish()
                # HTTP 命令 + face_result（主线程消费队列，电脑端联机）
                self._process_http_commands()
                self._process_face_results()
                # 触摸屏（读触点，命中入队）/ 状态机业务 / 侧边栏
                if self._wifi_mode:
                    # WiFi 设置全屏界面：自己处理触摸和绘制，不画状态机和侧边栏
                    self._handle_wifi_settings()
                elif self._threshold_mode:
                    # 阈值设置全屏界面：自己处理触摸和绘制
                    self._handle_threshold_settings()
                else:
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
            if self._ovr:
                self._ovr.destroy()
        except Exception:
            pass
        try:
            if self._voice:
                self._voice.destroy()
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
            if self._buzzer:
                self._buzzer.destroy()
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
