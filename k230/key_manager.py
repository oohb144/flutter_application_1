"""
亚博智能 K230 人脸识别智能系统 - 按键管理模块（CanMV）

由 maix-dostudy 移植。
差异：MaixPy4 的 key.Key(回调) 线程回调 -> K230 用 Pin 轮询。
- 豪华版板载 KEY 接 GPIO21（上拉，按下为低电平 value()==0）
- 在 update() 中轮询电平，用 time.ticks_ms 判短按/长按/超长按
- 事件入队，update() 弹出调用回调（与参考代码一致，保证主线程执行）
"""

import time
from ybUtils.YbKey import YbKey
import config


class KeyManager:
    """
    按键管理器（轮询模式，使用亚博 YbKey 官方封装，Pin61 板载 KEY）

    事件：
    - short_press: 短按（按下时长 < LONG_PRESS_MS）
    - long_press:  长按（按下时长 >= LONG_PRESS_MS，在释放前触发一次）
    - exit_press:  超长按（按下时长 >= EXIT_PRESS_MS，触发一次）
    """

    def __init__(self, on_short_press=None, on_long_press=None, on_exit_press=None,
                 long_press_ms=None, exit_press_ms=None, pin_num=None):
        self._on_short_press = on_short_press
        self._on_long_press = on_long_press
        self._on_exit_press = on_exit_press
        self._long_press_ms = long_press_ms if long_press_ms else config.LONG_PRESS_MS
        self._exit_press_ms = exit_press_ms if exit_press_ms else config.EXIT_PRESS_MS

        # 按键状态
        self._key_pressed = False
        self._press_start_time = 0
        self._long_press_triggered = False
        self._exit_press_triggered = False
        self._last_event_time = 0
        self._debounce_ms = config.KEY_DEBOUNCE_MS

        # 事件队列
        self._events = []

        # 使用亚博官方 YbKey（Pin61，上拉，低电平有效）
        try:
            print("[按键] 准备创建 YbKey...")
            self._key = YbKey()
            print("[按键] 按键管理器初始化完成 (YbKey, Pin61)")
        except Exception as e:
            print(f"[按键] YbKey 初始化失败: {e}")
            self._key = None

    def _read_pressed(self):
        """读取按键是否按下（YbKey.is_pressed 低电平有效）"""
        if self._key is None:
            return False
        try:
            return self._key.is_pressed()
        except Exception:
            return False

    def update(self):
        """
        轮询按键状态，更新事件队列并执行回调。
        必须在主循环中频繁调用。
        """
        # 先处理待办事件
        self._dispatch_events()

        if self._key is None:
            return

        now = time.ticks_ms()
        pressed = self._read_pressed()

        if pressed and not self._key_pressed:
            # 下降沿：开始按下
            self._key_pressed = True
            self._press_start_time = now
            self._long_press_triggered = False
            self._exit_press_triggered = False
            print(f"[按键] 下降沿检测 (pressed={self._key.is_pressed() if self._key else '?'})")

        if self._key_pressed:
            duration = time.ticks_diff(now, self._press_start_time)

            # 超长按检测（优先，按下期间触发一次）
            if (not self._exit_press_triggered) and duration >= self._exit_press_ms:
                self._exit_press_triggered = True
                self._events.append('exit_press')
                print(f"[按键] 超长按检测（{duration}ms），退出程序")

            # 长按检测（按下期间触发一次，且未超长按）
            elif (not self._long_press_triggered and
                  not self._exit_press_triggered and
                  duration >= self._long_press_ms):
                self._long_press_triggered = True
                self._events.append('long_press')
                print(f"[按键] 长按检测（{duration}ms）")

        if (not pressed) and self._key_pressed:
            # 上升沿：释放
            self._key_pressed = False
            duration = time.ticks_diff(now, self._press_start_time)

            # 防抖
            if time.ticks_diff(now, self._last_event_time) < self._debounce_ms:
                return

            # 已触发长按或超长按，则释放不再触发短按
            if self._long_press_triggered or self._exit_press_triggered:
                return

            # 短按
            if duration < self._long_press_ms:
                self._last_event_time = now
                self._events.append('short_press')
                print(f"[按键] 短按检测（{duration}ms）")

    def _dispatch_events(self):
        """弹出并执行事件回调"""
        while self._events:
            event = self._events.pop(0)
            try:
                if event == 'short_press' and self._on_short_press:
                    self._on_short_press()
                elif event == 'long_press' and self._on_long_press:
                    self._on_long_press()
                elif event == 'exit_press' and self._on_exit_press:
                    self._on_exit_press()
            except Exception as e:
                print(f"[按键] {event} 回调异常: {e}")

    def set_short_press_callback(self, callback):
        self._on_short_press = callback

    def set_long_press_callback(self, callback):
        self._on_long_press = callback

    def set_exit_press_callback(self, callback):
        self._on_exit_press = callback

    def is_pressed(self):
        return self._key_pressed

    def destroy(self):
        self._key = None
        print("[按键] 按键管理器已销毁")
