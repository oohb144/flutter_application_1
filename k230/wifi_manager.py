"""
亚博智能 K230 人脸识别智能系统 - WiFi 连接管理（CanMV）

开机连接路由器，拿到 IP 后返回。失败超时返回 None。
使用 network.WLAN(network.STA_IF)。
"""

import time
import network
import config


def connect_wifi(ssid=None, password=None, timeout_sec=None):
    """
    连接 WiFi（阻塞直到拿到 IP 或超时）

    返回:
        (ip_str, wlan_obj)  成功
        (None, wlan_obj)    失败
    """
    ssid = ssid if ssid is not None else config.WIFI_SSID
    password = password if password is not None else config.WIFI_PASSWORD
    timeout_sec = timeout_sec if timeout_sec is not None else config.WIFI_TIMEOUT_SEC

    print(f"[WiFi] 正在连接: {ssid}")
    try:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        # 已连接则先断开，避免重复连接卡住
        try:
            if wlan.isconnected():
                wlan.disconnect()
        except Exception:
            pass
        wlan.connect(ssid, password)

        start = time.ticks_ms()
        while True:
            try:
                if wlan.isconnected():
                    ip = wlan.ifconfig()[0]
                    print(f"[WiFi] 已连接, IP={ip}")
                    return (ip, wlan)
            except Exception:
                pass
            if time.ticks_diff(time.ticks_ms(), start) >= timeout_sec * 1000:
                print(f"[WiFi] 连接超时（{timeout_sec}s）")
                return (None, wlan)
            time.sleep_ms(500)

    except Exception as e:
        print(f"[WiFi] 连接异常: {e}")
        return (None, None)


def disconnect_wifi(wlan):
    """断开 WiFi"""
    if wlan is None:
        return
    try:
        wlan.disconnect()
        wlan.active(False)
        print("[WiFi] 已断开")
    except Exception as e:
        print(f"[WiFi] 断开异常: {e}")
