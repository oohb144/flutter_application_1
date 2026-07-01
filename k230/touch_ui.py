"""
亚博智能 K230 人脸识别智能系统 - 触摸屏 GUI（CanMV，OSD 轻量方案）

豪华版 ST7701 640x480 触摸屏，右侧侧边栏 5 按钮（主页/识别/录入/停止/推流）。
用 OSD 画按钮（draw_rectangle + draw_string_advanced）+ TOUCH(0) 读触点 + 矩形命中判断，
不引入 LVGL（LVGL 的 task_handler 会与人脸 PipeLine 抢 GIL/Display 通道）。

参考：G:\\data\\K230视觉模块\\程序源码\\15.touch\\2.enhanced_touch_drawing.py
坐标：TOUCH pt.x/pt.y 已映射到 640x480，与 osd_img 坐标一致，无需额外映射。

命中按钮 -> 回调 on_command(cmd_id)，复用 serial_voice.RecvCmd 命令码，
入主循环 _cmd_queue 与串口命令统一处理。
"""

from machine import TOUCH
import time
from serial_voice import RecvCmd


class Button:
    """侧边栏按钮（矩形区域 + 标签 + 命令码）"""

    def __init__(self, rect, label, cmd_id, color=(60, 60, 60)):
        self.rect = rect            # [x, y, w, h]
        self.label = label
        self.cmd_id = cmd_id
        self.color = color

    def hit(self, x, y):
        r = self.rect
        return r[0] <= x <= r[0] + r[2] and r[1] <= y <= r[1] + r[3]

    def draw(self, img, active=False):
        c = (0, 120, 220) if active else self.color
        # 填充 + 边框
        img.draw_rectangle(self.rect[0], self.rect[1], self.rect[2], self.rect[3],
                           color=c, thickness=-1)
        img.draw_rectangle(self.rect[0], self.rect[1], self.rect[2], self.rect[3],
                           color=(200, 200, 200), thickness=2)
        # 文字
        img.draw_string_advanced(self.rect[0] + 10, self.rect[1] + 15, 22,
                                 self.label, color=(255, 255, 255))


class TouchUI:
    """触摸屏侧边栏 UI"""

    def __init__(self, on_command):
        self._tp = TOUCH(0)
        self._on_command = on_command     # 回调(cmd_id)
        self._active_cmd = None           # 当前高亮按钮的 cmd_id
        self._buttons = [
            Button([520, 10, 110, 50],  "主页", RecvCmd.HOME),
            Button([520, 70, 110, 50],  "识别", RecvCmd.RECOGNIZE),
            Button([520, 130, 110, 50], "录入", RecvCmd.ENROLL),
            Button([520, 190, 110, 50], "停止", RecvCmd.STOP),
            Button([520, 250, 110, 50], "推流", RecvCmd.RTSP_TOGGLE),
            Button([520, 310, 110, 50], "语音", RecvCmd.VOICE_START,
                   color=(40, 120, 80)),
            Button([520, 370, 110, 50], "WiFi", RecvCmd.WIFI_SETTINGS,
                   color=(40, 80, 140)),
            Button([520, 430, 110, 50], "阈值", RecvCmd.THRESHOLD_SETTINGS,
                   color=(120, 40, 120)),
        ]
        # DOWN 事件常量（上板确认；EVENT_DOWN 不存在则退化为 0）
        self._down_evt = getattr(TOUCH, "EVENT_DOWN", 0)
        self._last_evt = None   # 上次事件，用于 DOWN 上升沿触发
        self._last_trigger = 0  # 上次触发时间，用于去抖（防一次按下抖动报多次）
        print("[触摸UI] 初始化完成，侧边栏 8 按钮（含语音/WiFi/阈值）")

    def set_active(self, cmd_id):
        """设置当前高亮按钮（状态切换时调）"""
        self._active_cmd = cmd_id

    def update(self):
        """主循环每帧调：读触摸，DOWN 上升沿命中按钮则回调（避免按住重复触发）"""
        try:
            pts = self._tp.read(1)
            if not pts:
                self._last_evt = None
                return
            pt = pts[0]
            evt = pt.event
            is_down = (evt == self._down_evt)
            # 边沿触发：只在 DOWN 上升沿（上次非 DOWN）触发一次，
            # 避免手指按住期间每帧重复触发 toggle 类命令
            if is_down and self._last_evt != self._down_evt:
                # 时间去抖：一次按下接触抖动可能报多次 DOWN 上升沿，
                # 300ms 内只触发一次
                now = time.ticks_ms()
                if time.ticks_diff(now, self._last_trigger) >= 300:
                    self._last_trigger = now
                    for b in self._buttons:
                        if b.hit(pt.x, pt.y):
                            print(f"[触摸UI] 命中: {b.label} ({b.cmd_id:#04x})")
                            if self._on_command:
                                try:
                                    self._on_command(b.cmd_id)
                                except Exception as e:
                                    print(f"[触摸UI] 回调异常: {e}")
                            break
            self._last_evt = evt
        except Exception:
            pass

    def draw(self, img):
        """画侧边栏到 OSD 图像（主循环状态机绘制之后调，画在最上层）"""
        for b in self._buttons:
            try:
                b.draw(img, active=(b.cmd_id == self._active_cmd))
            except Exception:
                pass
