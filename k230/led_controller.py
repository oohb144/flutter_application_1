"""
亚博智能 K230 人脸识别智能系统 - LED 控制模块（CanMV）

由 maix-dostudy 移植。
差异：MaixPy4 gpio.GPIO 单色 -> K230 YbRGB (WS2812 可变色)。
- 识别中：绿快闪
- 录制中：红慢闪
- 空闲：  蓝超慢闪
- 录入中：黄常亮（或闪烁）
- 成功提示：白闪 N 次
- 错误：  紫闪

非阻塞闪烁 update() 逻辑保留，需在主循环调用。
"""

import time
from ybUtils.YbRGB import YbRGB
import config


class LedController:
    """LED 控制器（YbRGB WS2812）"""

    def __init__(self):
        self._rgb = None
        self._blink_interval = 0
        self._blink_timer = 0
        self._blink_state = False
        self._is_blinking = False
        self._current_color = config.LED_COLOR_OFF
        # 常亮模式颜色（on/off 用）
        self._solid_color = config.LED_COLOR_OFF

        try:
            print("[LED] 准备创建 YbRGB...")
            self._rgb = YbRGB(num_leds=1)
            print("[LED] YbRGB 创建完成，开始自检闪烁...")
            # 开机自检：闪两下白
            self._set(config.LED_COLOR_SUCCESS)
            time.sleep_ms(80)
            self._set(config.LED_COLOR_OFF)
            time.sleep_ms(80)
            self._set(config.LED_COLOR_SUCCESS)
            time.sleep_ms(80)
            self._set(config.LED_COLOR_OFF)
            print("[LED] LED 控制器初始化完成 (YbRGB)")
        except Exception as e:
            print(f"[LED] YbRGB 初始化失败: {e}")
            self._rgb = None

    def _set(self, color):
        """直接设置 LED 颜色"""
        if self._rgb is None:
            return
        try:
            self._rgb.show_rgb((color[0], color[1], color[2]))
            self._current_color = color
        except Exception as e:
            print(f"[LED] 设置颜色异常: {e}")

    def on(self, color=None):
        """常亮（可选颜色）"""
        if self._rgb is None:
            return
        self._is_blinking = False
        self._solid_color = color if color else config.LED_COLOR_SUCCESS
        self._set(self._solid_color)

    def off(self):
        """熄灭"""
        if self._rgb is None:
            return
        self._is_blinking = False
        self._set(config.LED_COLOR_OFF)

    def blink(self, color, interval_ms):
        """
        启动闪烁
        color: 闪烁颜色
        interval_ms: 闪烁间隔（毫秒）
        """
        if self._rgb is None:
            return
        if (not self._is_blinking or
                self._blink_interval != interval_ms or
                self._current_color != color):
            self._blink_interval = interval_ms
            self._blink_timer = time.ticks_ms()
            self._blink_state = False
            self._is_blinking = True
            self._solid_color = color
            self._set(config.LED_COLOR_OFF)

    def set_state(self, state):
        """
        根据系统状态设置 LED 模式（便捷方法）
        state: config.State 枚举值
        """
        from config import State
        if state == State.RECOGNIZING:
            self.blink(config.LED_COLOR_RECOGNIZE, config.LED_BLINK_FAST)
        elif state in (State.RECORDING, State.MANUAL_RECORDING):
            self.blink(config.LED_COLOR_RECORD, config.LED_BLINK_SLOW)
        elif state == State.ENROLLING:
            self.on(config.LED_COLOR_ENROLL)
        elif state == State.ERROR:
            self.blink(config.LED_COLOR_ERROR, config.LED_BLINK_FAST)
        else:  # IDLE 等
            self.blink(config.LED_COLOR_IDLE, config.LED_BLINK_IDLE)

    def update(self):
        """非阻塞闪烁更新，主循环调用"""
        if self._rgb is None or not self._is_blinking:
            return
        now = time.ticks_ms()
        if time.ticks_diff(now, self._blink_timer) >= self._blink_interval:
            self._blink_timer = now
            self._blink_state = not self._blink_state
            self._set(self._solid_color if self._blink_state else config.LED_COLOR_OFF)

    def flash(self, color=None, times=3, interval_ms=100):
        """阻塞闪烁指定次数"""
        color = color if color else config.LED_COLOR_SUCCESS
        for _ in range(times):
            self._set(color)
            time.sleep_ms(interval_ms)
            self._set(config.LED_COLOR_OFF)
            time.sleep_ms(interval_ms)

    def is_blinking(self):
        return self._is_blinking

    def is_on(self):
        return self._current_color != config.LED_COLOR_OFF

    def destroy(self):
        self.off()
        print("[LED] LED 控制器已销毁")
