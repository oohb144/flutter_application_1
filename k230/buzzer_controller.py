"""
亚博智能 K230 人脸识别智能系统 - 蜂鸣器控制模块（CanMV）

由 LedController 思路派生：用亚博 YbBuzzer（Pin53，PWM 通道5）。
- 切换界面/状态：短促"嘀"（TRANSITION_FREQ）
- 连上 WiFi / 录入成功 / 识别到熟人：成功音（SUCCESS_FREQ）
- 识别到陌生人脸：报警音（ALARM_FREQ，稍长）

鸣响策略：短促阻塞鸣响（与 LedController.flash 同风格），仅在事件边沿触发，
不每帧响，避免与人脸识别抢 GIL / 卡顿。BUZZER_ENABLE=False 时全部静音。

频率/时长常量见 config.py（ALARM_* / SUCCESS_* / TRANSITION_*）。
"""

import time
from ybUtils.YbBuzzer import YbBuzzer
import config


class BuzzerController:
    """蜂鸣器控制器（YbBuzzer，PWM 驱动）"""

    def __init__(self):
        self._bz = None
        if not getattr(config, "BUZZER_ENABLE", True):
            print("[蜂鸣器] 已禁用 (BUZZER_ENABLE=False)")
            return
        try:
            print("[蜂鸣器] 准备创建 YbBuzzer...")
            self._bz = YbBuzzer()
            # 开机自检：短嘀一声（确认硬件就绪）
            self._beep(config.TRANSITION_FREQ, config.TRANSITION_DURATION)
            print("[蜂鸣器] YbBuzzer 初始化完成 (Pin53, PWM5)")
        except Exception as e:
            print(f"[蜂鸣器] YbBuzzer 初始化失败（不影响识别）: {e}")
            self._bz = None

    def _beep(self, freq, duration_ms):
        """底层鸣响：指定频率/时长（duration_ms 毫秒）"""
        if self._bz is None:
            return
        try:
            # on(freq, duty, duration) duration 单位为秒
            self._bz.on(freq=freq, duty=50, duration=duration_ms / 1000.0)
        except Exception:
            # 个别固件 on 签名不同，退回默认 beep()
            try:
                self._bz.beep()
            except Exception as e:
                print(f"[蜂鸣器] 鸣响异常: {e}")

    def beep_transition(self):
        """切换界面/状态：短促提示音"""
        self._beep(config.TRANSITION_FREQ, config.TRANSITION_DURATION)

    def beep_success(self):
        """成功提示（连上 WiFi / 录入成功 / 识别到熟人）"""
        self._beep(config.SUCCESS_FREQ, config.SUCCESS_DURATION)

    def beep_alarm(self):
        """报警：识别到陌生人脸"""
        self._beep(config.ALARM_FREQ, config.ALARM_DURATION)

    def beep_wifi(self):
        """WiFi 连接成功（与成功音同，独立方法便于日后区分音色）"""
        self._beep(config.SUCCESS_FREQ, config.SUCCESS_DURATION)

    def off(self):
        """立即静音"""
        if self._bz is None:
            return
        try:
            self._bz.off()
        except Exception:
            pass

    def destroy(self):
        self.off()
        print("[蜂鸣器] 蜂鸣器控制器已销毁")
