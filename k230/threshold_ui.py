"""
亚博智能 K230 人脸识别智能系统 - 阈值设置 UI（CanMV，全屏 OSD + 加减按钮）

触摸侧边栏「阈值」按钮进入。三组阈值各自 [-]/[+] 调整，实时生效，返回时保存到
/data/threshold.json（下次开机自动加载覆盖 config 默认值）。

布局（640x480 全屏）：
  [20,10]   标题
  [20,72]   分隔线
  行1 y=90  识别相似度（主）  [-][+]
  行2 y=200 检测置信度        [-][+]
  行3 y=310 IoU 阈值          [-][+]
  [260,410] 返回按钮（保存）
"""

import json
import config

THRESHOLD_PATH = "/data/threshold.json"

# 每项: (key, 显示标签, 最小, 最大, 步长)
_ITEMS = [
    ("recognize", "识别相似度", 0.30, 0.90, 0.02),
    ("conf",      "检测置信度", 0.10, 0.90, 0.05),
    ("iou",       "IoU 阈值",   0.10, 0.50, 0.05),
]

# 各行起点 y
_ROW_YS = [90, 200, 310]

# [-]/[+] 按钮几何
_BTN_W = 90
_BTN_H = 60
_BTN_MINUS_X = 420
_BTN_PLUS_X = 520


class ThresholdUI:
    """阈值设置 UI（全屏 OSD）"""

    def __init__(self, app):
        self._app = app
        # 当前值（从 config 默认起步；进入界面时从 face 实例同步实时值）
        self._vals = {
            "recognize": config.FACE_RECOGNIZE_THRESHOLD,
            "conf":      config.FACE_CONF_THRESHOLD,
            "iou":       config.FACE_IOU_THRESHOLD,
        }
        print("[阈值UI] 初始化完成")

    # ==================== 入口/退出 ====================
    def enter(self):
        """进入界面：从 face 实例同步当前生效的阈值"""
        try:
            f = self._app._face
            self._vals["recognize"] = f._recognize_th
            self._vals["conf"] = f._detect_conf_th
            self._vals["iou"] = f._detect_iou_th
        except Exception as e:
            print("[阈值UI] 同步阈值失败: " + str(e))

    def exit(self):
        """退出界面：持久化保存"""
        self._save()

    # ==================== 持久化 ====================
    def _save(self):
        try:
            with open(THRESHOLD_PATH, "w") as f:
                f.write(json.dumps(self._vals))
            print("[阈值UI] 已保存: " + str(self._vals))
        except Exception as e:
            print("[阈值UI] 保存失败: " + str(e))

    # ==================== 绘制 ====================
    def draw(self, img):
        # 背景
        img.draw_rectangle(0, 0, 640, 480, color=(30, 30, 30), thickness=-1)
        # 标题
        img.draw_string_advanced(20, 10, 28, "阈值设置",
                                 color=config.TEXT_COLOR_WHITE)
        img.draw_string_advanced(260, 16, 16, "[-]/[+] 调整, 返回保存",
                                 color=(160, 160, 160))
        # 分隔线
        img.draw_line(20, 72, 620, 72, color=(100, 100, 100), thickness=1)

        for i, (key, label, lo, hi, step) in enumerate(_ITEMS):
            y = _ROW_YS[i]
            v = self._vals[key]
            # 标签
            img.draw_string_advanced(20, y, 22, label,
                                     color=config.TEXT_COLOR_WHITE)
            # 当前值（大字）
            img.draw_string_advanced(20, y + 30, 40, "{:.2f}".format(v),
                                     color=(0, 255, 200))
            # 范围提示
            img.draw_string_advanced(170, y + 42, 14,
                                     "范围 {:.2f}~{:.2f}".format(lo, hi),
                                     color=(120, 120, 120))
            # [-] [+] 按钮
            self._draw_btn(img, _BTN_MINUS_X, y + 10, _BTN_W, _BTN_H,
                           "-", (140, 40, 40))
            self._draw_btn(img, _BTN_PLUS_X, y + 10, _BTN_W, _BTN_H,
                           "+", (40, 140, 40))

        # 返回按钮（居中底部）
        self._draw_btn(img, 260, 410, 120, 50, "返回", (40, 80, 160))

    def _draw_btn(self, img, x, y, w, h, text, bg):
        img.draw_rectangle(x, y, w, h, color=bg, thickness=-1)
        img.draw_rectangle(x, y, w, h, color=(150, 150, 150), thickness=1)
        # 文字居中（近似）
        fs = 26 if len(text) <= 2 else 22
        tx = x + (w - len(text) * (fs // 2)) // 2
        ty = y + (h - fs) // 2
        img.draw_string_advanced(tx, ty, fs, text, color=config.TEXT_COLOR_WHITE)

    # ==================== 触摸处理 ====================
    def handle_touch(self, pt, is_down_edge):
        """DOWN 上升沿命中调用，返回 'exit' 表示请求退出界面"""
        if not is_down_edge:
            return None
        x, y = pt.x, pt.y

        # 返回按钮
        if 260 <= x <= 380 and 410 <= y <= 460:
            return "exit"

        # 各行 [-] [+]
        for i, (key, label, lo, hi, step) in enumerate(_ITEMS):
            by = _ROW_YS[i] + 10
            if _BTN_MINUS_X <= x <= _BTN_MINUS_X + _BTN_W and by <= y <= by + _BTN_H:
                self._adjust(key, -step, lo, hi)
                return None
            if _BTN_PLUS_X <= x <= _BTN_PLUS_X + _BTN_W and by <= y <= by + _BTN_H:
                self._adjust(key, +step, lo, hi)
                return None
        return None

    def _adjust(self, key, delta, lo, hi):
        """调整某项阈值并实时应用到 FaceDetector"""
        v = self._vals[key] + delta
        if v < lo:
            v = lo
        if v > hi:
            v = hi
        v = round(v, 2)   # 浮点圆整，避免累积误差
        if v == self._vals[key]:
            return   # 到边界无变化
        self._vals[key] = v
        # 实时生效
        try:
            f = self._app._face
            if key == "recognize":
                f.set_detect_threshold(recognize_th=v)
            elif key == "conf":
                f.set_detect_threshold(conf_th=v)
            elif key == "iou":
                f.set_detect_threshold(iou_th=v)
        except Exception as e:
            print("[阈值UI] 应用失败: " + str(e))
        # 轻提示音
        try:
            self._app._buzz("beep_transition")
        except Exception:
            pass


def load_saved_threshold():
    """开机时从 /data/threshold.json 读保存的阈值，返回 dict 或 None"""
    try:
        with open(THRESHOLD_PATH, "r") as f:
            data = json.loads(f.read())
            if isinstance(data, dict):
                return data
            return None
    except Exception:
        return None
