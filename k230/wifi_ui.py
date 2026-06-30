"""
亚博智能 K230 人脸识别智能系统 - WiFi 设置 UI（CanMV，OSD 软键盘）

参考出厂示例 G:\\data\\K230视觉模块\\新建文件夹\\apps\\setting\\wifi_settings.py
（LVGL 实现），改用 OSD 绘制（避免 LVGL 与人脸 PipeLine 抢 GIL/Display 通道）。

功能：
  1. 扫描可用 WiFi 列表（含 RSSI/加密信息），按信号强度排序
  2. 触摸选中条目 -> 弹软键盘输入密码（QWERTY + 数字 + 退格 + Shift + Enter）
  3. 点连接 -> 起线程重连，不卡主循环
  4. 连接成功保存 ssid+password 到 /data/wifi_config.json，下次开机优先用
  5. 返回按钮退出

布局（640x480，全屏）：
  [0,10]    标题
  [0,50]    当前状态 / IP
  [20,85]   扫描 WiFi 列表（每条目高 45）
  [20,85..] 输入框（密码模式）  <- 仅 password 模式
  [0,260]   软键盘（高度 220）  <- 仅 password 模式
  [20,430]  返回按钮
  [360,430] 连接/扫描 按钮

移植自 LVGL: YbNetwork.SCAN_WIFI -> network.WLAN(STA_IF).scan()
"""

import _thread
import json
import os
import time
import network
import config

SAVED_WIFI_PATH = "/data/wifi_config.json"


class WiFiUI:
    """WiFi 设置 UI（全屏 OSD）"""

    # 状态
    MODE_LIST = "list"
    MODE_PASSWORD = "password"
    MODE_CONNECTING = "connecting"

    # 键盘模式
    KB_LOWER = "lower"
    KB_UPPER = "upper"
    KB_NUMBER = "number"

    # 键盘布局（特殊字符：^=shift <=backspace *=切换数字/字母 _=enter 空格长条）
    _KB_LAYOUTS = {
        "lower": [
            ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"],
            ["a", "s", "d", "f", "g", "h", "j", "k", "l"],
            ["^", "z", "x", "c", "v", "b", "n", "m", "<"],
            ["*", " ", "_"],
        ],
        "upper": [
            ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"],
            ["A", "S", "D", "F", "G", "H", "J", "K", "L"],
            ["^", "Z", "X", "C", "V", "B", "N", "M", "<"],
            ["*", " ", "_"],
        ],
        "number": [
            ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
            ["-", "/", ":", ";", "(", ")", "$", "&", "@", "\""],
            ["^", ".", ",", "?", "!", "'", "<"],
            ["*", " ", "_"],
        ],
    }

    # 布局常量
    LIST_X = 20
    LIST_W = 600
    LIST_Y = 90
    LIST_ITEM_H = 45
    INPUT_Y = 220
    KB_Y = 260
    BTN_Y = 430

    def __init__(self, app):
        self._app = app
        self._mode = self.MODE_LIST
        self._scan_results = []            # [{ssid, rssi, security}]
        self._selected_ssid = None         # 当前选中条目
        self._input_buffer = ""            # 输入的密码
        self._kb_mode = self.KB_LOWER      # 当前键盘模式
        self._status_text = ""
        self._status_color = (150, 150, 150)
        self._connecting_ssid = None
        print("[WiFiUI] 初始化完成")

    # ==================== 入口/退出 ====================
    def enter(self):
        self._mode = self.MODE_LIST
        self._selected_ssid = None
        self._input_buffer = ""
        self._kb_mode = self.KB_LOWER
        self._connecting_ssid = None
        self._update_status_from_app()
        # 自动扫描一次
        self._do_scan()

    def exit(self):
        self._mode = self.MODE_LIST
        self._selected_ssid = None
        self._input_buffer = ""

    # ==================== 绘制 ====================
    def draw(self, img):
        """主循环调：绘制全屏 OSD"""
        # 背景
        img.draw_rectangle(0, 0, 640, 480, color=(30, 30, 30), thickness=-1)
        # 标题
        img.draw_string_advanced(20, 10, 28, "WiFi 设置",
                                 color=config.TEXT_COLOR_WHITE)
        # 状态
        img.draw_string_advanced(20, 45, 18, self._status_text,
                                 color=self._status_color)
        # 分隔线
        img.draw_line(20, 72, 620, 72, color=(100, 100, 100), thickness=1)

        if self._mode == self.MODE_LIST:
            self._draw_list(img)
        elif self._mode in (self.MODE_PASSWORD, self.MODE_CONNECTING):
            self._draw_password_mode(img)

        # 底部按钮
        self._draw_bottom_buttons(img)

    def _draw_list(self, img):
        """绘制扫描结果列表"""
        if not self._scan_results:
            img.draw_string_advanced(self.LIST_X, self.LIST_Y + 20, 20,
                                     "扫描中... 或附近无 WiFi",
                                     color=(180, 180, 180))
            return
        # 顶部提示
        img.draw_string_advanced(self.LIST_X, self.LIST_Y - 18, 14,
                                 "触摸选中 -> 输入密码 -> 连接",
                                 color=(180, 180, 180))
        # 条目（最多显示 6 个，避免溢出）
        for i, net in enumerate(self._scan_results[:6]):
            y = self.LIST_Y + i * self.LIST_ITEM_H
            sel = (self._selected_ssid == net["ssid"])
            bg = (0, 120, 220) if sel else (50, 50, 50)
            img.draw_rectangle(self.LIST_X, y, self.LIST_W, self.LIST_ITEM_H - 2,
                               color=bg, thickness=-1)
            img.draw_rectangle(self.LIST_X, y, self.LIST_W, self.LIST_ITEM_H - 2,
                               color=(120, 120, 120), thickness=1)
            # SSID
            img.draw_string_advanced(self.LIST_X + 10, y + 10, 22,
                                     net["ssid"][:25],
                                     color=config.TEXT_COLOR_WHITE)
            # RSSI 信号强度（简化显示）
            rssi = net.get("rssi", -100)
            if rssi > -50:
                sig_txt = "强"
                sig_c = config.TEXT_COLOR_GREEN
            elif rssi > -70:
                sig_txt = "中"
                sig_c = config.TEXT_COLOR_YELLOW
            else:
                sig_txt = "弱"
                sig_c = config.TEXT_COLOR_RED
            img.draw_string_advanced(self.LIST_X + self.LIST_W - 80, y + 10, 18,
                                     sig_txt + " " + str(rssi),
                                     color=sig_c)
            # 加密锁图标
            if net.get("security", True):
                img.draw_string_advanced(self.LIST_X + self.LIST_W - 120, y + 10, 18,
                                         "[锁]", color=(180, 180, 180))
            # 当前连接中
            if self._connecting_ssid == net["ssid"]:
                img.draw_string_advanced(self.LIST_X + 300, y + 10, 18,
                                         "连接中...",
                                         color=config.TEXT_COLOR_YELLOW)

    def _draw_password_mode(self, img):
        """密码输入模式：输入框 + 软键盘"""
        # 当前连接的 SSID 提示
        img.draw_string_advanced(self.LIST_X, self.LIST_Y, 22,
                                 "SSID: " + (self._selected_ssid or ""),
                                 color=config.TEXT_COLOR_WHITE)
        # 输入框
        img.draw_rectangle(self.LIST_X, self.INPUT_Y, self.LIST_W, 35,
                           color=(20, 20, 20), thickness=-1)
        img.draw_rectangle(self.LIST_X, self.INPUT_Y, self.LIST_W, 35,
                           color=(150, 150, 150), thickness=1)
        img.draw_string_advanced(self.LIST_X + 5, self.INPUT_Y - 18, 14,
                                 "密码", color=(180, 180, 180))
        # 显示密码（明文，方便看）
        display_pwd = self._input_buffer if self._input_buffer else ""
        img.draw_string_advanced(self.LIST_X + 10, self.INPUT_Y + 8, 20,
                                 display_pwd if display_pwd else " ",
                                 color=config.TEXT_COLOR_WHITE)
        # 软键盘
        self._draw_keyboard(img)

    def _draw_keyboard(self, img):
        """绘制软键盘（4 行）"""
        layout = self._KB_LAYOUTS.get(self._kb_mode, self._KB_LAYOUTS["lower"])
        kb_x = 10
        kb_y = self.KB_Y
        key_h = 40
        key_w = 55
        for row_idx, row in enumerate(layout):
            y = kb_y + row_idx * (key_h + 4)
            # 计算行宽，居中
            row_keys = len(row)
            # 特殊处理：最后行的空格占 5 格宽
            total_w = 0
            for k in row:
                if k == " ":
                    total_w += key_w * 5
                else:
                    total_w += key_w
            x = kb_x + (self.LIST_W - total_w) // 2
            for k in row:
                w = key_w * 5 if k == " " else key_w
                # 特殊键颜色
                if k == "^":
                    bg = (100, 100, 100)
                    txt = "Shift"
                elif k == "<":
                    bg = (100, 100, 100)
                    txt = "Del"
                elif k == "_":
                    bg = (40, 140, 40)
                    txt = "OK"
                elif k == "*":
                    bg = (100, 100, 100)
                    txt = "123" if self._kb_mode != self.KB_NUMBER else "ABC"
                elif k == " ":
                    bg = (80, 80, 80)
                    txt = "space"
                else:
                    bg = (60, 60, 60)
                    txt = k
                img.draw_rectangle(x, y, w - 2, key_h, color=bg, thickness=-1)
                img.draw_rectangle(x, y, w - 2, key_h,
                                   color=(150, 150, 150), thickness=1)
                # 文字居中（近似）
                txt_x = x + (w - len(txt) * 10) // 2
                img.draw_string_advanced(txt_x, y + 10, 18, txt,
                                         color=config.TEXT_COLOR_WHITE)
                x += w

    def _draw_bottom_buttons(self, img):
        # 返回按钮（左下，红）
        img.draw_rectangle(20, self.BTN_Y, 120, 40, color=(140, 40, 40),
                           thickness=-1)
        img.draw_string_advanced(50, self.BTN_Y + 10, 22, "返回",
                                 color=config.TEXT_COLOR_WHITE)
        # 右侧按钮：list 模式 = "扫描"，password 模式 = "连接"
        if self._mode == self.MODE_LIST:
            img.draw_rectangle(480, self.BTN_Y, 140, 40,
                               color=(40, 100, 160), thickness=-1)
            img.draw_string_advanced(510, self.BTN_Y + 10, 22, "刷新扫描",
                                     color=config.TEXT_COLOR_WHITE)
        elif self._mode == self.MODE_PASSWORD:
            img.draw_rectangle(480, self.BTN_Y, 140, 40,
                               color=(40, 140, 40), thickness=-1)
            img.draw_string_advanced(510, self.BTN_Y + 10, 22, "连接",
                                     color=config.TEXT_COLOR_WHITE)

    # ==================== 触摸处理 ====================
    def handle_touch(self, pt, is_down_edge):
        """处理触摸命中（DOWN 上升沿调用）"""
        if not is_down_edge:
            return
        x, y = pt.x, pt.y

        if self._mode == self.MODE_LIST:
            self._handle_list_touch(x, y)
        elif self._mode == self.MODE_PASSWORD:
            if not self._handle_keyboard_touch(x, y):
                # 不在键盘区，再判断列表/按钮
                self._handle_list_touch(x, y)

        # 底部按钮（任何模式都响应）
        # 返回
        if 20 <= x <= 140 and self.BTN_Y <= y <= self.BTN_Y + 40:
            if self._mode == self.MODE_PASSWORD:
                # 密码模式返回 -> 列表模式
                self._mode = self.MODE_LIST
                self._input_buffer = ""
            else:
                # 列表模式返回 -> 通知 app 退出 WiFi 界面
                return "exit"
            return
        # 右按钮
        if 480 <= x <= 620 and self.BTN_Y <= y <= self.BTN_Y + 40:
            if self._mode == self.MODE_LIST:
                self._do_scan()
            elif self._mode == self.MODE_PASSWORD:
                self._do_connect()
            return
        return None

    def _handle_list_touch(self, x, y):
        """列表条目触摸命中"""
        if not (self.LIST_X <= x <= self.LIST_X + self.LIST_W
                and self.LIST_Y <= y <= self.LIST_Y + 6 * self.LIST_ITEM_H):
            return
        idx = (y - self.LIST_Y) // self.LIST_ITEM_H
        if 0 <= idx < len(self._scan_results[:6]):
            net = self._scan_results[idx]
            self._selected_ssid = net["ssid"]
            print("[WiFiUI] 选中: " + net["ssid"])
            # 有密码的条目 -> 进入密码输入模式
            if net.get("security", True):
                self._mode = self.MODE_PASSWORD
                self._input_buffer = ""
                self._kb_mode = self.KB_LOWER
            else:
                # 无密码直接连
                self._input_buffer = ""
                self._do_connect()

    def _handle_keyboard_touch(self, x, y):
        """键盘触摸命中，返回 True 表示命中键盘区"""
        if not (0 <= x <= 640 and self.KB_Y <= y <= self.KB_Y + 4 * 44):
            return False
        layout = self._KB_LAYOUTS.get(self._kb_mode, self._KB_LAYOUTS["lower"])
        kb_x = 10
        key_h = 40
        key_w = 55
        row_idx = (y - self.KB_Y) // (key_h + 4)
        if row_idx < 0 or row_idx >= len(layout):
            return False
        row = layout[row_idx]
        # 计算行起始 x（居中）
        total_w = 0
        for k in row:
            if k == " ":
                total_w += key_w * 5
            else:
                total_w += key_w
        cur_x = kb_x + (self.LIST_W - total_w) // 2
        for k in row:
            w = key_w * 5 if k == " " else key_w
            if cur_x <= x <= cur_x + w - 2:
                self._on_key_press(k)
                return True
            cur_x += w
        return False

    def _on_key_press(self, key):
        """键盘按键处理"""
        if key == "^":  # Shift 切换大小写
            if self._kb_mode == self.KB_LOWER:
                self._kb_mode = self.KB_UPPER
            elif self._kb_mode == self.KB_UPPER:
                self._kb_mode = self.KB_LOWER
        elif key == "<":  # Backspace
            if self._input_buffer:
                self._input_buffer = self._input_buffer[:-1]
        elif key == "_":  # Enter / 确认连接
            self._do_connect()
        elif key == "*":  # 切换数字/字母
            if self._kb_mode == self.KB_NUMBER:
                self._kb_mode = self.KB_LOWER
            else:
                self._kb_mode = self.KB_NUMBER
        elif key == " ":
            self._input_buffer += " "
        else:
            if len(self._input_buffer) < 64:
                self._input_buffer += key
                # 小写键盘按一个字母后自动切回小写（与手机键盘习惯不同，更简单）
                if self._kb_mode == self.KB_UPPER and key.isalpha():
                    self._kb_mode = self.KB_LOWER

    # ==================== 业务逻辑 ====================
    def _do_scan(self):
        """扫描可用 WiFi。优先用 ybUtils.YbNetwork（出厂封装），fallback 用标准 network。"""
        print("[WiFiUI] 开始扫描 WiFi...")
        raw = None
        use_ybnet = False
        try:
            # 优先尝试 YbNetwork（出厂封装，scan 行为稳定）
            from ybUtils.YbNetwork import YbNetwork
            ybnet = YbNetwork()
            raw = ybnet.SCAN_WIFI()
            use_ybnet = True
            print("[WiFiUI] YbNetwork.scan 返回 " + str(len(raw) if raw else 0) + " 项")
        except Exception as e:
            print("[WiFiUI] YbNetwork 不可用: " + str(e) + "，fallback 标准 network")
            use_ybnet = False
            try:
                wlan = network.WLAN(0)
                print("[WiFiUI] wlan.active=" + str(wlan.active())
                      + " isconnected=" + str(wlan.isconnected()))
                if not wlan.active():
                    wlan.active(True)
                    import time as _time
                    _time.sleep_ms(500)
                raw = wlan.scan()
                print("[WiFiUI] network.WLAN.scan 返回 " + str(len(raw) if raw else 0) + " 项")
                # 调试：打印原始返回结构
                if raw:
                    print("[WiFiUI] 第一项原始: " + str(raw[0]))
            except Exception as e2:
                print("[WiFiUI] network.WLAN.scan 失败: " + str(e2))
                raw = []

        if not raw:
            self._scan_results = []
            print("[WiFiUI] 扫描结果为空")
            return

        seen = set()
        nets = []
        for item in raw:
            try:
                if use_ybnet or hasattr(item, "ssid"):
                    # YbNetwork 返回对象：item.ssid / item.rssi / item.security
                    ssid_b = item.ssid
                    ssid = ssid_b.decode("utf-8") if isinstance(ssid_b, bytes) else str(ssid_b)
                    rssi = getattr(item, "rssi", -100)
                    security = getattr(item, "security", True)
                else:
                    # 标准 scan() 返回 tuple
                    ssid_b = item[0]
                    ssid = ssid_b.decode("utf-8") if isinstance(ssid_b, bytes) else str(ssid_b)
                    rssi = item[3] if len(item) > 3 else -100
                    authmode = item[4] if len(item) > 4 else 0
                    security = (authmode != 0)

                if not ssid or ssid in seen:
                    continue
                seen.add(ssid)
                nets.append({"ssid": ssid, "rssi": rssi, "security": security})
            except Exception as e:
                print("[WiFiUI] 解析项失败: " + str(e))
                continue
        nets.sort(key=lambda n: n["rssi"], reverse=True)
        self._scan_results = nets
        print("[WiFiUI] 扫描完成，发现 " + str(len(nets)) + " 个 WiFi")
        if nets:
            for n in nets[:3]:
                print("[WiFiUI]   " + n["ssid"] + " rssi=" + str(n["rssi"]))

    def _do_connect(self):
        """起线程连接 WiFi"""
        if not self._selected_ssid:
            return
        ssid = self._selected_ssid
        pwd = self._input_buffer
        self._connecting_ssid = ssid
        self._status_text = "连接中: " + ssid + "..."
        self._status_color = config.TEXT_COLOR_YELLOW
        print("[WiFiUI] 开始连接: " + ssid)

        def _thread_fn():
            try:
                from wifi_manager import connect_wifi
                ip, wlan = connect_wifi(ssid, pwd,
                                        timeout_sec=getattr(config, "WIFI_TIMEOUT_SEC", 20))
                if ip:
                    self._save_wifi_config(ssid, pwd)
                    self._app._ip = ip
                    self._app._wlan = wlan
                    self._status_text = "已连接 " + ssid + " IP=" + ip
                    self._status_color = config.TEXT_COLOR_GREEN
                    if self._app._rtsp:
                        self._app._rtsp.set_ip(ip)
                    # 蜂鸣器：WiFi 连接成功提示音
                    if getattr(self._app, "_buzzer", None):
                        try:
                            self._app._buzzer.beep_wifi()
                        except Exception:
                            pass
                    print("[WiFiUI] 连接成功: " + ip)
                else:
                    self._status_text = "连接失败: " + ssid
                    self._status_color = config.TEXT_COLOR_RED
                    print("[WiFiUI] 连接失败")
                self._connecting_ssid = None
            except Exception as e:
                self._status_text = "异常: " + str(e)
                self._status_color = config.TEXT_COLOR_RED
                self._connecting_ssid = None
                print("[WiFiUI] 连接异常: " + str(e))

        _thread.start_new_thread(_thread_fn, ())

    def _save_wifi_config(self, ssid, pwd):
        """保存 ssid+pwd 到 /data/wifi_config.json"""
        try:
            # MicroPython 的 os 无 path 子模块，用 try open 检查文件存在
            data = {}
            try:
                with open(SAVED_WIFI_PATH, "r") as f:
                    data = json.loads(f.read())
            except Exception:
                pass  # 文件不存在或解析失败，从空 dict 开始
            data[ssid] = pwd
            with open(SAVED_WIFI_PATH, "w") as f:
                f.write(json.dumps(data))
            print("[WiFiUI] 已保存配置: " + ssid)
        except Exception as e:
            print("[WiFiUI] 保存配置失败: " + str(e))

    def _update_status_from_app(self):
        """从 app 当前状态初始化状态文字"""
        if self._app._ip:
            self._status_text = "当前 IP: " + self._app._ip
            self._status_color = config.TEXT_COLOR_BLUE
        else:
            self._status_text = "未连接"
            self._status_color = (150, 150, 150)


def load_saved_wifi():
    """开机时从 /data/wifi_config.json 读保存的 WiFi 配置。
    返回 (ssid, password) 或 None。优先用最近连接的（文件最后一个）。"""
    try:
        with open(SAVED_WIFI_PATH, "r") as f:
            data = json.loads(f.read())
        if not data:
            return None
        # 取最后一个（最近保存的）
        ssid = list(data.keys())[-1]
        return (ssid, data[ssid])
    except Exception:
        return None
