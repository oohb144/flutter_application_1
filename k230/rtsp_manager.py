"""
亚博智能 K230 人脸识别智能系统 - RTSP 推流管理（CanMV，VENC link 直推原始画面）

sensor CHN0(YUV420 640x480) --link--> VENC H264 --> RTSP server
推 sensor 原始画面（不带 OSD 人脸框），rtsp://<IP>:8554/test

关键改进（吸取 WBCRtsp.py 的 RtspServer 模式，解决旧 rtsp_manager 卡死）：
  - GetStream(timeout=0) 非阻塞取码流，不抢 GIL，不饿死主循环
  - sendvideodata_byphyaddr 物理地址直发，不 uctypes 拷贝整包数据
  - 推流线程 sleep_ms(1) 让步
旧版卡死根因：阻塞 GetStream + uctypes.bytearray_at 整包拷贝 + sleep_ms(2) 让步不足。

不抓 VO writeback（WBC），故无绿边/OSD 闪烁/旋转尺寸问题。
不与 Display 抢 CHN0：sensor CHN0 同时绑 Display(VIDEO1) 和 VENC，多 sink 共享。
"""

import _thread
import os
import time
from media.media import *
from media.vencoder import *     # Encoder, ChnAttrStr, VENC_CHN_ID_0, VENC_DEV_ID, VIDEO_ENCODE_MOD_ID, StreamData
# mm 模块（rtsp_server / multi_media_type）：yahboom 固件路径与官方不同，尝试多种
mm = None
for _mod in ('media.multimedia', 'multimedia', 'media.mm', 'media.mmf'):
    try:
        _m = __import__(_mod, globals(), locals(), ['rtsp_server'], 0)
        if hasattr(_m, 'rtsp_server'):
            mm = _m
            print("[RTSP] mm 导入成功: " + _mod)
            break
    except Exception:
        continue
if mm is None:
    print("[RTSP] mm 导入失败（rtsp_server 不可用）")
import config


def _align_up(x, align=16):
    return (x + align - 1) & ~(align - 1)


class RtspManager:
    """RTSP 推流管理器（VENC link 直推 sensor 原始画面）"""

    # 与 WBCRtsp.py RtspServer 默认 session_name 一致 → URL rtsp://<IP>:8554/test
    _PATH = "test"

    def __init__(self, sensor=None, ip=None, port=8554, path="test"):
        self._sensor = sensor
        self._ip = ip
        self._port = port
        self._path = path
        self._running = False
        self._url = ""
        self._encoder = None
        self._rtspserver = None
        self._link = None
        self._venc_chn = VENC_CHN_ID_0
        self._width = _align_up(getattr(config, 'RTSP_WIDTH', 640), 16)
        self._height = getattr(config, 'RTSP_HEIGHT', 480)

    @staticmethod
    def configure_before_pipeline():
        """占位：推原始画面模式不抓 VO writeback，无需 WBCRtsp.configure。
        保留方法签名以兼容 main.py 调用点（实际 no-op）。"""
        print("[RTSP] 推原始画面模式，无需 WBC configure")
        return True

    def set_ip(self, ip):
        self._ip = ip
        if self._running:
            self._url = self._build_url()

    def _build_url(self):
        if self._ip:
            return "rtsp://" + self._ip + ":" + str(self._port) + "/" + self._path
        return "rtsp://<IP>:" + str(self._port) + "/" + self._path

    def start(self):
        if self._running:
            return True
        if mm is None:
            print("[RTSP] mm 不可用，无法启动")
            return False
        if self._sensor is None:
            print("[RTSP] 无 sensor，无法启动")
            return False
        try:
            # 1. 编码器 + 输出缓冲
            self._encoder = Encoder()
            self._encoder.SetOutBufs(self._venc_chn, getattr(config, 'RTSP_OUT_BUFS', 8),
                                     self._width, self._height)
            # 2. link sensor CHN0 -> VENC（与 Display 共享 CHN0，多 sink）
            self._link = MediaManager.link(
                self._sensor.bind_info()['src'],
                (VIDEO_ENCODE_MOD_ID, VENC_DEV_ID, self._venc_chn))
            # 3. 编码通道（bit_rate 参考 WBCRtsp.py RtspServer）
            chnAttr = ChnAttrStr(self._encoder.PAYLOAD_TYPE_H264,
                                 self._encoder.H264_PROFILE_MAIN,
                                 self._width, self._height, bit_rate=2048)
            self._encoder.Create(self._venc_chn, chnAttr)
            # 4. RTSP server
            self._rtspserver = mm.rtsp_server()
            self._rtspserver.rtspserver_init(self._port)
            self._rtspserver.rtspserver_createsession(
                self._path, mm.multi_media_type.media_h264, False)
            self._rtspserver.rtspserver_start()
            # 5. 开始编码（sensor CHN0 帧 auto 流入 VENC）
            self._encoder.Start(self._venc_chn)
            self._running = True
            _thread.start_new_thread(self._stream_loop, ())
            self._url = self._build_url()
            print("[RTSP] 启动成功: " + self._url + " (" + str(self._width) + "x"
                  + str(self._height) + " H264 原始画面)")
            return True
        except Exception as e:
            print("[RTSP] 启动失败: " + str(e))
            self._running = False
            self._cleanup()
            return False

    def _stream_loop(self):
        """推流线程：非阻塞取码流 + 物理地址直发（不拷贝、不抢 GIL）"""
        streamData = StreamData()
        dbg_cnt = 0
        while self._running:
            try:
                os.exitpoint()
                # timeout=0 非阻塞：无数据立即返回，不阻塞抢 GIL
                ret = self._encoder.GetStream(self._venc_chn, streamData, timeout=0)
                if ret == 0 and streamData.pack_cnt > 0:
                    pcnt = streamData.pack_cnt
                    dbg_cnt += 1
                    if dbg_cnt % 200 == 1:
                        print("[RTSP] 推流 dbg: frame#" + str(dbg_cnt)
                              + " pack_cnt=" + str(pcnt))
                    # 关键：一帧可能多 NAL 包（I 帧 8 包、P 帧 1-3 包），
                    # 必须逐包发送，只发 [0] 会导致 VLC 花屏/绿屏/跳帧。
                    for pack_idx in range(pcnt):
                        try:
                            self._rtspserver.rtspserver_sendvideodata_byphyaddr(
                                self._path,
                                streamData.phy_addr[pack_idx],
                                streamData.data_size[pack_idx], 1000)
                        except Exception:
                            pass
                    try:
                        self._encoder.ReleaseStream(self._venc_chn, streamData)
                    except Exception:
                        pass
                    time.sleep_ms(1)  # 让步，避免空转饿死主线程
                else:
                    time.sleep_ms(2)
            except BaseException as e:
                if dbg_cnt % 200 == 1:
                    print("[RTSP] 推流循环异常(继续): " + str(e))
                time.sleep_ms(5)
        print("[RTSP] 推流线程退出, 共 " + str(dbg_cnt) + " 帧")

    def _cleanup(self):
        """清理半初始化/已分配资源"""
        try:
            if self._encoder:
                try: self._encoder.Stop(self._venc_chn)
                except Exception: pass
                try: self._encoder.Destroy(self._venc_chn)
                except Exception: pass
        except Exception: pass
        try:
            if self._link is not None:
                del self._link
        except Exception: pass
        self._link = None
        try:
            if self._rtspserver:
                try: self._rtspserver.rtspserver_stop()
                except Exception: pass
                try: self._rtspserver.rtspserver_deinit()
                except Exception: pass
        except Exception: pass
        self._encoder = None
        self._rtspserver = None

    def stop(self):
        if not self._running and self._encoder is None:
            return True
        self._running = False
        try:
            time.sleep_ms(200)  # 等推流线程退出
        except Exception:
            pass
        self._cleanup()
        return True

    def is_running(self):
        return self._running

    def get_url(self):
        return self._url

    def toggle(self):
        if self._running:
            self.stop()
        else:
            self.start()
        return self._running
