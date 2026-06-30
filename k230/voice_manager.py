"""
亚博智能 K230 人脸识别智能系统 - 语音管理器（CanMV，离线关键词识别）

双模式（自动选择可用方案）：
  1. speech_recognizer 离线多词识别（优先）
     - 可设多个中文关键词，各自映射命令
     - 固件需 speech_recognizer 模块（部分 CanMV 固件未提供）
  2. kws.kmodel 唤醒词（回退）
     - 唤醒词固化"小南小南"（二分类模型，不可改词）
     - 检测到唤醒词后按当前状态循环切换：待机→识别→录入→待机
     - 需要 PyAudio 采 PCM + aidemo.kws_preprocess + KPU 推理

使用方式：触摸"语音"按钮 → start_listening() → 监听关键词 → 回调命令 → 自动停止。
按需启动，不常驻，避免与人脸识别持续抢 GIL/KPU。

⚠️ 当前固件 (CanMV v1.4.3 yahboom) speech_recognizer 不可用，自动回退方案2。
   升级固件后若 speech_recognizer 可用，自动切换方案1（多词识别）。
"""

import _thread
import struct
import time
import os

import config

# ==================== kws.kmodel 回退方案所需的导入 ====================
_kws_available = False
try:
    import ulab.numpy as np
    import nncase_runtime as nn
    import aidemo
    from media.pyaudio import PyAudio, paInt16
    from libs.AIBase import AIBase
    _kws_available = True
except ImportError as e:
    print(f"[语音] kws 依赖缺失（回退方案不可用）: {e}")


# ==================== kws.kmodel 回退方案推理封装 ====================
if _kws_available:
    class _KWSApp(AIBase):
        """KWS 推理（照搬 05.keyword_spotting.py，唤醒词固化"小南小南"）"""

        def __init__(self, kmodel_path, threshold, debug_mode=0):
            super().__init__(kmodel_path)
            self.threshold = threshold
            self.debug_mode = debug_mode
            self.cache_np = np.zeros((1, 256, 105), dtype=np.float)
            self.fp = None

        def preprocess(self, pcm_data):
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
            logits_np = results[0]
            self.cache_np = results[1]
            max_logits = np.max(logits_np, axis=1)[0]
            max_p = np.max(max_logits)
            idx = np.argmax(max_logits)
            self._dbg_cnt = getattr(self, "_dbg_cnt", 0) + 1
            if self._dbg_cnt % 10 == 0:
                print("[KWS dbg] max_p=", float(max_p), " idx=", int(idx),
                      " thr=", self.threshold)
            if idx == 1:
                print("[KWS idx=1] max_p=", float(max_p), " thr=", self.threshold)
            if max_p > self.threshold and idx == 1:
                return 1
            return 0


# ==================== 语音管理器 ====================
class VoiceManager:
    """
    离线语音识别管理器

    优先 speech_recognizer（离线多词），不可用回退 kws.kmodel（唤醒词循环切态）。
    按需启动（按钮触发），超时自动停止，不常驻避免抢 GIL/KPU。
    """

    def __init__(self):
        self._mode = None             # "speech" / "kws" / None
        self._is_ready = False
        self._is_listening = False
        self._on_command = None       # 关键词命令回调(cmd_id)
        self._on_wake = None          # kws 唤醒词回调（无参数，主循环负责切态）

        # speech_recognizer 方案
        self._asr = None
        self._sr_module = None
        self._kw_list = []            # [(word, cmd_name), ...]

        # kws.kmodel 方案
        self._kws = None
        self._fp = None
        self._pyaudio = None
        self._stream = None
        self._kws_thread_running = False
        self._kws_paused = True       # 默认暂停，start_listening 时恢复
        self._last_trigger = 0

        # 超时控制
        self._listen_start_time = 0
        self._timeout_ms = getattr(config, "VOICE_LISTEN_TIMEOUT_MS", 5000)

    # ---------- 初始化（程序启动时调一次） ----------
    def start(self, on_command=None, on_wake=None):
        """
        初始化语音管理器。
        on_command(cmd_id): speech_recognizer 检测到关键词时调用（仅 speech 方案）。
        on_wake():          kws.kmodel 检测到唤醒词时调用（仅 kws 方案，无参数）。
                            回调在语音/KWS线程中，仅做入队操作。
        """
        if not getattr(config, "VOICE_MGR_ENABLE", False):
            print("[语音] 未启用 (VOICE_MGR_ENABLE=False)")
            return False
        self._on_command = on_command
        self._on_wake = on_wake
        # 优先 speech_recognizer
        if self._try_start_speech():
            return True
        # 回退 kws.kmodel
        if self._try_start_kws():
            return True
        print("[语音] 所有方案均不可用")
        return False

    # ---------- 方案1：speech_recognizer ----------
    def _try_start_speech(self):
        try:
            from speech_recognizer import rec_type, speech_recognizer, ASR
            self._sr_module = speech_recognizer

            asr = ASR(rec_type.CN_KEYWORD)
            asr.set_kws_res(0)   # 加载固件内置声学模型（不需要训练）

            kw_map = getattr(config, "VOICE_CMD_KEYWORDS", {})
            if not kw_map:
                print("[语音] VOICE_CMD_KEYWORDS 为空，跳过 speech_recognizer")
                return False

            self._kw_list = list(kw_map.items())
            for i, (word, _cmd_name) in enumerate(self._kw_list):
                asr.set_kws_word(i, word, 0.7)
                print(f"[语音]   注册关键词[{i}]: '{word}' -> {_cmd_name}")

            asr.set_kws_full_cn_kw_cb(self._speech_callback)
            speech_recognizer.set_kws_obj(asr)
            self._asr = asr
            self._mode = "speech"
            self._is_ready = True
            print("[语音] speech_recognizer 就绪（" + str(len(self._kw_list)) + " 个关键词）")
            return True
        except Exception as e:
            print(f"[语音] speech_recognizer 不可用: {e}")
            self._asr = None
            self._sr_module = None
            return False

    def _speech_callback(self, data):
        """speech_recognizer 回调（语音线程）：仅入队"""
        try:
            word_idx, word, score, start_ms, end_ms = data
            print(f"[语音] 识别到: '{word}' (idx={word_idx}, score={score})")
            if 0 <= word_idx < len(self._kw_list):
                _word, cmd_name = self._kw_list[word_idx]
                cmd_id = self._cmd_name_to_id(cmd_name)
                if cmd_id is not None and self._on_command:
                    self._on_command(cmd_id)
        except Exception as e:
            print(f"[语音] speech 回调异常: {e}")

    # ---------- 方案2：kws.kmodel ----------
    def _try_start_kws(self):
        if not _kws_available:
            print("[语音] kws 依赖缺失（numpy/nn/aidemo/pyaudio）")
            return False
        kmodel_path = "/sdcard/kmodel/kws.kmodel"
        try:
            os.stat(kmodel_path)
        except OSError:
            print(f"[语音] {kmodel_path} 不存在")
            return False

        try:
            self._fp = aidemo.kws_fp_create()
            threshold = getattr(config, "VOICE_WAKE_THRESHOLD", 0.5)
            self._kws = _KWSApp(kmodel_path, threshold)
            self._kws.fp = self._fp

            chunk_ms = 300
            sample_rate = 16000
            chunk = int(chunk_ms / 1000 * sample_rate)

            self._pyaudio = PyAudio()
            self._pyaudio.initialize(chunk)
            self._stream = self._pyaudio.open(
                format=paInt16, channels=1, rate=sample_rate,
                input=True, frames_per_buffer=chunk)
            try:
                self._stream.volume(vol=100)
            except Exception:
                pass

            self._mode = "kws"
            self._is_ready = True
            self._kws_paused = True   # 待命，start_listening 时才恢复
            self._kws_thread_running = True
            _thread.start_new_thread(self._kws_loop, ())

            wake = getattr(config, "VOICE_WAKE_WORD", "小南小南")
            print(f"[语音] kws.kmodel 就绪，唤醒词='{wake}'（固化），检测到后循环切态")
            return True
        except Exception as e:
            print(f"[语音] kws.kmodel 启动失败: {e}")
            self._cleanup_kws_resources()
            return False

    def _kws_loop(self):
        """KWS 推理线程：仅在 _kws_paused=False 时推理，否则休眠"""
        print("[语音] KWS 线程启动")
        loop_cnt = 0
        while self._kws_thread_running:
            if self._kws_paused:
                time.sleep_ms(100)
                continue
            try:
                pcm = self._stream.read()
                loop_cnt += 1
                # 诊断：每 5 帧打印 PCM 幅度
                if loop_cnt % 5 == 0:
                    max_amp = 0
                    if pcm:
                        for j in range(0, min(len(pcm), 200), 2):
                            v = struct.unpack("<h", pcm[j:j + 2])[0]
                            av = v if v > 0 else -v
                            if av > max_amp:
                                max_amp = av
                    print("[mic] loop#", loop_cnt, " max_amp=", max_amp)
                if pcm:
                    res = self._kws.run(pcm)
                    if res:
                        now = time.ticks_ms()
                        debounce = getattr(config, "TRIGGER_DEBOUNCE_MS", 1500)
                        if time.ticks_diff(now, self._last_trigger) >= debounce:
                            self._last_trigger = now
                            print("[语音] 唤醒: 小南小南")
                            self._on_kws_wake()
            except Exception as e:
                print(f"[语音] KWS 循环异常: {e}")
                time.sleep_ms(50)
        print("[语音] KWS 线程退出")

    def _on_kws_wake(self):
        """kws 唤醒词检测命中：调 _on_wake 回调（运行在 KWS 线程，仅入队）"""
        if self._on_wake:
            try:
                self._on_wake()
            except Exception as e:
                print(f"[语音] _on_wake 回调异常: {e}")

    # ---------- 按需启停（触摸按钮触发） ----------
    def start_listening(self):
        """开始监听（按需启动，超时自动停止）"""
        if not self._is_ready:
            print("[语音] 未就绪")
            return False
        if self._is_listening:
            print("[语音] 已在监听中")
            return True

        self._is_listening = True
        self._listen_start_time = time.ticks_ms()

        if self._mode == "kws":
            self._kws_paused = False
            print("[语音] KWS 开始监听（说 '" +
                  getattr(config, "VOICE_WAKE_WORD", "小南小南") + "'）")
        elif self._mode == "speech":
            # speech_recognizer 在 set_kws_obj 后已持续监听，
            # _is_listening 仅控制超时自动停止
            print("[语音] speech 开始监听（说关键词）")
        return True

    def stop_listening(self):
        """停止监听"""
        if not self._is_listening:
            return
        self._is_listening = False
        if self._mode == "kws":
            self._kws_paused = True
            print("[语音] KWS 已停止监听（让出 KPU）")
        elif self._mode == "speech":
            print("[语音] speech 已停止监听")

    def is_listening(self):
        return self._is_listening

    def pause(self):
        """暂停所有语音推理（推流时调用，释放 KPU/麦克风给 VENC）"""
        if not self._is_ready:
            return
        self._is_listening = False
        if self._mode == "kws":
            self._kws_paused = True
            print("[语音] 已暂停（让出 KPU/麦克风给推流）")

    def resume(self):
        """恢复语音推理（推流关闭后调用）"""
        if not self._is_ready:
            return
        if self._mode == "kws":
            # 不自动恢复监听，等按钮触发
            print("[语音] 已恢复（可按语音按钮开始监听）")

    def is_ready(self):
        return self._is_ready

    def get_mode(self):
        """返回当前方案名称：'speech' / 'kws' / None"""
        return self._mode

    def check_timeout(self):
        """主循环每帧调用：超时自动停止监听"""
        if not self._is_listening:
            return
        elapsed = time.ticks_diff(time.ticks_ms(), self._listen_start_time)
        if elapsed >= self._timeout_ms:
            print(f"[语音] 监听超时（{self._timeout_ms}ms），自动停止")
            self.stop_listening()

    # ---------- 命令名 -> RecvCmd 映射 ----------
    @staticmethod
    def _cmd_name_to_id(name):
        """命令名（config 中的字符串）转 RecvCmd 整数命令码"""
        try:
            from serial_voice import RecvCmd
        except ImportError:
            return None
        mapping = {
            "home": RecvCmd.HOME,
            "recognize": RecvCmd.RECOGNIZE,
            "enroll": RecvCmd.ENROLL,
            "stop": RecvCmd.STOP,
            "rtsp_on": RecvCmd.RTSP_ON,
            "rtsp_off": RecvCmd.RTSP_OFF,
            "rtsp_toggle": RecvCmd.RTSP_TOGGLE,
            "enroll_capture": RecvCmd.ENROLL_CAPTURE,
            "wifi_settings": RecvCmd.WIFI_SETTINGS,
        }
        return mapping.get(name)

    # ---------- 资源释放 ----------
    def _cleanup_kws_resources(self):
        """释放 kws.kmodel 方案的所有资源"""
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

    def destroy(self):
        """释放全部资源（程序退出时调）"""
        self._is_listening = False
        self._kws_thread_running = False
        time.sleep_ms(200)   # 等 KWS 线程退出
        self._cleanup_kws_resources()
        self._asr = None
        self._sr_module = None
        self._is_ready = False
        self._mode = None
        print("[语音] 资源已释放")


# ==================== 独立测试入口 ====================
if __name__ == "__main__":
    os.exitpoint(os.EXITPOINT_ENABLE)
    try:
        from media.media import MediaManager
        MediaManager.init()
    except Exception:
        pass

    def on_cmd(cmd_id):
        print(f"  >>> [测试] 收到命令: {cmd_id:#04x}")

    vm = VoiceManager()
    ok = vm.start(on_command=on_cmd)
    print(f"[测试] 初始化结果: {ok}, 方案: {vm.get_mode()}")

    if ok:
        print("=" * 50)
        print("按 Enter 开始监听（5秒超时自动停止）")
        print("Ctrl+C 退出")
        print("=" * 50)
        try:
            while True:
                os.exitpoint()
                vm.check_timeout()
                # 简易交互：每 5 秒自动触发一轮监听
                if not vm.is_listening():
                    vm.start_listening()
                time.sleep_ms(100)
        except KeyboardInterrupt:
            pass
        finally:
            vm.destroy()
    try:
        from media.media import MediaManager
        MediaManager.deinit()
    except Exception:
        pass
