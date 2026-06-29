"""
亚博智能 K230 人脸识别智能系统 - 离线语音唤醒模块（CanMV，kws.kmodel 版）

当前固件 (CanMV v1.4.3 yahboom 2026-01-20) 经上板诊断：
  speech_recognizer ❌、media.audio ❌（k230 skill 文档/keyword_recording 例程是旧固件）
  aidemo ✅、nncase_runtime ✅、media.pyaudio ✅，/sdcard/kmodel/kws.kmodel ✅

故采用 05.keyword_spotting.py 的方案：
  PyAudio 采 PCM → aidemo.kws_preprocess 提特征 → kws.kmodel 跑 KPU → 后处理 idx==1 唤醒
  唤醒词固化"小南小南"（模型内二分类，不可改词）。

⚠️ 占 KPU！必须时分复用：IDLE 态跑 KWS，识别/录入态 pause() 让出 KPU 给人脸。
  （multi_kws.kmodel 出厂自带但全例程无引用，多词用法未知，留待后续上板探索）

接口：start(callback, init_media=) / pause / resume / stop / destroy
- KWS 在独立线程循环推理，回调里只做轻量入队（线程安全）
- 同一唤醒词带去抖（TRIGGER_DEBOUNCE_MS）
"""

import _thread
import struct
import time
import os
import ulab.numpy as np
import nncase_runtime as nn
import aidemo
from media.pyaudio import PyAudio, paInt16
from media.media import MediaManager
from libs.AIBase import AIBase
import config


class _KWSApp(AIBase):
    """KWS 推理封装（照搬 05.keyword_spotting.py 的 KWSApp）"""

    def __init__(self, kmodel_path, threshold, debug_mode=0):
        super().__init__(kmodel_path)
        self.threshold = threshold
        self.debug_mode = debug_mode
        # 模型状态缓存（KWS 是流式模型，需跨帧保留状态）
        self.cache_np = np.zeros((1, 256, 105), dtype=np.float)
        # 音频特征处理器，由 VoiceRecognition.start 创建后注入
        self.fp = None

    def preprocess(self, pcm_data):
        """PCM bytes → 模型输入 tensor（音频特征 + 上帧状态）"""
        pcm_data_list = []
        for i in range(0, len(pcm_data), 2):
            int_pcm = struct.unpack("<h", pcm_data[i:i + 2])[0]
            pcm_data_list.append(float(int_pcm))
        mp_feats = aidemo.kws_preprocess(self.fp, pcm_data_list)[0]
        mp_feats_np = np.array(mp_feats).reshape((1, 30, 40))
        audio_tensor = nn.from_numpy(mp_feats_np)
        cache_tensor = nn.from_numpy(self.cache_np)
        return [audio_tensor, cache_tensor]

    def postprocess(self, results):
        """后处理：idx==1 且 max_p>阈值 = 检测到唤醒词"小南小南" """
        logits_np = results[0]
        self.cache_np = results[1]          # 更新状态供下帧
        max_logits = np.max(logits_np, axis=1)[0]
        max_p = np.max(max_logits)
        idx = np.argmax(max_logits)
        # 诊断：每 10 帧打印一次模型输出，看喊词时 max_p/idx 是否变化
        self._dbg_cnt = getattr(self, "_dbg_cnt", 0) + 1
        if self._dbg_cnt % 10 == 0:
            print("[KWS dbg] max_p=", float(max_p), " idx=", int(idx),
                  " thr=", self.threshold)
        # 诊断：idx==1（疑似唤醒）的帧总打印，看喊词时 max_p 峰值
        if idx == 1:
            print("[KWS idx=1] max_p=", float(max_p), " thr=", self.threshold)
        if max_p > self.threshold and idx == 1:
            return 1
        return 0


class VoiceRecognition:
    """离线关键词唤醒（kws.kmodel，固化'小南小南'，占 KPU）"""

    def __init__(self, kmodel_path=None, threshold=None, debounce_ms=None,
                 sample_rate=16000, chunk_ms=300):
        self._kmodel_path = kmodel_path or "/sdcard/kmodel/kws.kmodel"
        self._threshold = (threshold if threshold is not None
                           else getattr(config, "VOICE_THRESHOLD", 0.5))
        self._debounce_ms = debounce_ms or getattr(config, "TRIGGER_DEBOUNCE_MS", 1500)
        self._sample_rate = sample_rate
        self._chunk = int(chunk_ms / 1000 * sample_rate)
        self._kws = None
        self._fp = None
        self._pyaudio = None
        self._stream = None
        self._callback = None
        self._is_running = False
        self._paused = False
        self._last_trigger = 0

    def start(self, callback=None, init_media=False):
        """
        启动 KWS 监听线程。
        callback: 唤醒时被调用，接收 "wake"
        init_media: 是否在本模块内调 MediaManager.init()。
                    独立运行时 True；集成到 main（PipeLine.create 已 init）时 False。
        """
        if self._is_running:
            print("[语音] 已在运行")
            return True
        self._callback = callback
        try:
            # 顺序照搬 05.keyword_spotting: fp -> PyAudio -> initialize -> MediaManager.init -> open
            self._fp = aidemo.kws_fp_create()
            self._pyaudio = PyAudio()
            self._pyaudio.initialize(self._chunk)
            if init_media:
                MediaManager.init()
            self._stream = self._pyaudio.open(
                format=paInt16, channels=1, rate=self._sample_rate,
                input=True, frames_per_buffer=self._chunk)
            try:
                self._stream.volume(vol=100)
            except Exception:
                pass
            # KWS 推理器
            self._kws = _KWSApp(self._kmodel_path, self._threshold)
            self._kws.fp = self._fp
            self._is_running = True
            _thread.start_new_thread(self._loop, ())
            print(f"[语音] KWS 已启动: model={self._kmodel_path} threshold={self._threshold} 唤醒词='小南小南'(固化)")
            return True
        except Exception as e:
            print(f"[语音] 启动失败: {e}")
            self._is_running = False
            self.destroy()
            return False

    def _loop(self):
        """KWS 推理线程：读 PCM → KPU 推理 → 命中则回调"""
        print("[语音] KWS 线程启动")
        self._loop_cnt = 0
        while self._is_running:
            if self._paused:
                time.sleep_ms(100)
                continue
            try:
                pcm = self._stream.read()
                self._loop_cnt += 1
                # 诊断：每 5 帧打印 PCM 最大幅度，确认麦克风是否真的收到声音
                # 安静时 max_amp 应 <200，喊时应 >1000
                if self._loop_cnt % 5 == 0:
                    max_amp = 0
                    if pcm:
                        for j in range(0, len(pcm), 2):
                            v = struct.unpack("<h", pcm[j:j+2])[0]
                            if v > max_amp:
                                max_amp = v
                            elif -v > max_amp:
                                max_amp = -v
                    print("[mic] loop#", self._loop_cnt, " max_amp=", max_amp)
                if pcm:
                    res = self._kws.run(pcm)
                    if res:
                        now = time.ticks_ms()
                        if time.ticks_diff(now, self._last_trigger) >= self._debounce_ms:
                            self._last_trigger = now
                            print("[语音] 唤醒: 小南小南")
                            if self._callback:
                                try:
                                    self._callback("wake")
                                except Exception as e:
                                    print(f"[语音] 回调异常: {e}")
            except Exception as e:
                print(f"[语音] KWS 循环异常: {e}")
                time.sleep_ms(50)
        print("[语音] KWS 线程退出")

    def pause(self):
        """暂停 KWS 推理（释放 KPU 给人脸识别）"""
        self._paused = True
        print("[语音] KWS 已暂停（让出 KPU）")

    def resume(self):
        """恢复 KWS 推理"""
        self._paused = False
        print("[语音] KWS 已恢复")

    def is_running(self):
        return self._is_running

    def stop(self):
        if not self._is_running:
            return
        self._is_running = False
        time.sleep_ms(200)   # 等线程退出

    def destroy(self):
        """逆序释放：kws → stream → pyaudio → fp"""
        self.stop()
        try:
            if self._kws is not None:
                self._kws.deinit()
        except Exception:
            pass
        self._kws = None
        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
        except Exception:
            pass
        self._stream = None
        try:
            if self._pyaudio is not None:
                self._pyaudio.terminate()
        except Exception:
            pass
        self._pyaudio = None
        try:
            if self._fp is not None:
                aidemo.kws_fp_destroy(self._fp)
        except Exception:
            pass
        self._fp = None
        print("[语音] 资源已释放")


# ==================== 独立测试入口 ====================
if __name__ == "__main__":
    # 上板单独运行，验证 KWS 唤醒通路（不依赖人脸识别/主程序）
    os.exitpoint(os.EXITPOINT_ENABLE)
    nn.shrink_memory_pool()

    def on_wake(cmd):
        print(f"  >>> [测试] 收到命令: {cmd}")

    vr = VoiceRecognition(threshold=0.3)   # 测试降阈值，看喊词时 max_p 峰值
    if not vr.start(callback=on_wake, init_media=True):
        print("[测试] 启动失败，退出")
        raise SystemExit

    print("=" * 50)
    print("KWS 唤醒测试中，请喊 '小南小南' ...")
    print("（Ctrl+C 退出）")
    print("=" * 50)

    try:
        while True:
            os.exitpoint()
            time.sleep_ms(200)
    except KeyboardInterrupt:
        pass
    finally:
        vr.destroy()
        try:
            MediaManager.deinit()
        except Exception:
            pass
