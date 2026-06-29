"""
亚博智能 K230 人脸识别智能系统 - 配置文件（CanMV MicroPython）

由 maix-dostudy (MaixPy4) 移植而来。
- 路径调整为 K230 标准：模型在 /sdcard/kmodel/，数据在 /data/
- 硬件：豪华版 ST7701 LCD 640x480
- 模型：出厂自带 face_detection_320.kmodel / face_recognition.kmodel / prior_data_320.bin
"""

from micropython import const  # noqa: used for IntEnum-like constants

# ==================== 硬件配置 ====================
# 豪华版 LCD（ST7701，640x480）
DISPLAY_TYPE = "st7701"  # "st7701"(豪华版LCD) / "virt"(无屏虚拟)
DISPLAY_WIDTH = 640
DISPLAY_HEIGHT = 480

# AI 通道分辨率（RGB888，送人脸检测模型，宽 16 对齐）
RGB888P_WIDTH = 640
RGB888P_HEIGHT = 480

# 按键引脚（豪华版板载 KEY，默认 GPIO21）
KEY_PIN = 21

# LED：使用亚博 YbRGB（WS2812，可变色）。None 表示用普通 GPIO 单色 LED
LED_TYPE = "ybrgb"  # "ybrgb" / "gpio"
LED_GPIO_PIN = 52  # 备用：普通 LED 引脚

# ==================== 模型路径 ====================
# 人脸检测模型（320x320，anchor-based，需 prior_data_320.bin）
FACE_DETECT_MODEL = "/sdcard/kmodel/face_detection_320.kmodel"
FACE_DETECT_ANCHORS = "/sdcard/utils/prior_data_320.bin"
FACE_DETECT_INPUT_SIZE = [320, 320]  # [w, h]

# 人脸特征提取模型（112x112，输出 128/512 维特征）
FACE_RECOGNITION_MODEL = "/sdcard/kmodel/face_recognition.kmodel"
FACE_RECOGNITION_INPUT_SIZE = [112, 112]  # [w, h]

# ==================== 人脸识别阈值 ====================
# 检测置信度阈值（越大越严格）
FACE_CONF_THRESHOLD = 0.5
# 检测 IoU 阈值（NMS）
FACE_IOU_THRESHOLD = 0.2
# 识别相似度阈值（dot/2+0.5 后的值，越大越严格）
# crop 降级对齐精度有限，实测相似度 0.5-0.7，暂降到 0.60；后续用关键点仿射对齐可调回 0.72
FACE_RECOGNIZE_THRESHOLD = 0.60

# 录入结果显示时间（毫秒）
ENROLL_SHOW_TIME = 2000

# ==================== 文件路径 ====================
# 人脸数据库目录（每标签一个 .bin 特征文件）
FACES_DB_DIR = "/data/face_db/"
# 录制文件存储目录
RECORD_DIR = "/data/recordings/"

# ==================== 按键配置 ====================
# 长按阈值（毫秒）
LONG_PRESS_MS = 1500
# 超长按阈值（毫秒，触发退出）
EXIT_PRESS_MS = 3000
# 防抖（毫秒）
KEY_DEBOUNCE_MS = 200

# ==================== LED 闪烁配置 ====================
LED_BLINK_FAST = 200  # 识别中：绿快闪
LED_BLINK_SLOW = 500  # 录制中：红慢闪
LED_BLINK_IDLE = 1000  # 空闲：蓝超慢闪

# LED 颜色 (R, G, B) 0-255
LED_COLOR_RECOGNIZE = (0, 255, 0)  # 识别 - 绿
LED_COLOR_RECORD = (255, 0, 0)  # 录制 - 红
LED_COLOR_IDLE = (0, 0, 255)  # 空闲 - 蓝
LED_COLOR_ENROLL = (255, 255, 0)  # 录入 - 黄
LED_COLOR_SUCCESS = (255, 255, 255)  # 成功 - 白
LED_COLOR_ERROR = (255, 0, 255)  # 错误 - 紫
LED_COLOR_OFF = (0, 0, 0)

# ==================== 蜂鸣器/提示音 ====================
ALARM_FREQ = 2700
ALARM_DURATION = 300
SUCCESS_FREQ = 1500
SUCCESS_DURATION = 200
TRANSITION_FREQ = 2000
TRANSITION_DURATION = 150

# ==================== 显示颜色（RGB565/RGB888 通用） ====================
TEXT_COLOR_WHITE = (255, 255, 255)
TEXT_COLOR_GREEN = (0, 255, 0)
TEXT_COLOR_RED = (255, 0, 0)
TEXT_COLOR_YELLOW = (255, 255, 0)
TEXT_COLOR_BLUE = (0, 128, 255)

FACE_BOX_COLOR_KNOWN = (0, 255, 0)  # 熟人 - 绿
FACE_BOX_COLOR_UNKNOWN = (255, 0, 0)  # 陌生人 - 红
FACE_BOX_COLOR_DETECTED = (255, 255, 0)  # 仅检测（录入态）- 黄

# ==================== 功能开关 ====================
AUDIO_ENABLE = True
LED_ENABLE = True
VOICE_ENABLE = False  # 本机KWS（麦克风采集疑似有问题，跳过；voice_recognition.py 备用）
ONLINE_VOICE_ENABLE = False  # 在线语音（阶段4，默认关，见思路文档）
SERIAL_ENABLE = True  # 串口语音模块（外接 SU-03T/ASRPRO，K230 不碰音频）
SERIAL_BAUDRATE = 115200  # 串口波特率（语音模块端需一致）
TOUCH_ENABLE = True  # 触摸屏 GUI（豪华版 ST7701，OSD 侧边栏）
RTSP_ENABLE_DEFAULT = False  # 开机不自动开 RTSP，点按开启
STREAM_ENABLE = False  # HTTP JPEG 推流（阶段3）
AUTO_RECORD_ENABLE = False  # 自动录制（识别到人脸自动录）

# ==================== RTSP 推流配置 ====================
# VENC link 直推 sensor CHN0 原始画面（YUV420 640x480 -> H264 -> RTSP）。
# URL rtsp://<IP>:8554/test（session_name=test，与 WBCRtsp.py RtspServer 默认一致）。
# 推流线程用非阻塞 GetStream(timeout=0) + sendvideodata_byphyaddr 物理地址直发，
# 不拷贝、不抢 GIL，不卡死。不抓 VO writeback，故无绿边/OSD 闪烁。
RTSP_PATH = "test"  # session_name，电脑端拉流路径
RTSP_WIDTH = 640
RTSP_HEIGHT = 480
RTSP_AUDIO_ENABLE = (
    False  # 阶段A: 回退纯视频。带音频采集线程会与人脸识别抢 GIL 导致卡顿
)
# VENC 编码帧率（降低可减小码率/缓解反压；范围一般 15~30）
RTSP_FPS = 25
# VENC 输出缓冲帧数（卡顿时网络发送追不上的缓冲余量，4~8）
RTSP_OUT_BUFS = 8
# WBC（Write Back Composite）屏幕原始宽高，豪华版 ST7701 为 480x800
# WBCRtsp.configure 必须用屏幕物理尺寸，且在 pl.create 之前调
WBC_WIDTH = 480
WBC_HEIGHT = 800

# ==================== 录制配置 ====================
RECORD_VIDEO_FPS = 25
RECORD_AUDIO_SAMPLE_RATE = 16000
RECORD_AUDIO_CHANNEL = 1

# ==================== 状态服务器配置 ====================
STATUS_SERVER_PORT = 8001

# ==================== WiFi 配置（阶段2） ====================
WIFI_SSID = "码上同行的ICT"
WIFI_PASSWORD = "mstxdict"
# WiFi 连接超时（秒）
WIFI_TIMEOUT_SEC = 20

# WiFi 设置界面预设列表（触摸选中直接连；用户按需增删条目）
WIFI_PRESETS = [
    ("码上同行的ICT", "mstxdict"),
    ("MyHome",        "password123"),
    ("Office",        "office_wifi_pwd"),
]

# ==================== 语音识别配置（阶段4） ====================
# 单唤醒词（kws.kmodel，固化"小南小南"，占KPU需时分复用）
# 注：当前固件(CanMV v1.4.3 yahboom)无 speech_recognizer/media.audio，
# 只能用 kws.kmodel（二分类，唤醒词固化在模型里不可改）；multi_kws.kmodel 无例程待探索
VOICE_WAKE_WORD = "小南小南"  # 固化唤醒词（仅显示提示用，不可改）
VOICE_THRESHOLD = 0.5  # kws.kmodel 唤醒阈值（参考 05.keyword_spotting.py）
TRIGGER_DEBOUNCE_MS = 1500  # 同一唤醒词去抖窗口（毫秒）

# 离线关键词唤醒词 -> 命令名（多词阶段用，待上板验证 set_kws_word 多词）
VOICE_KEYWORDS = {
    "zhu3 jie4 mian4": "home",
    "lu4 ru4": "enroll",
    "shi2 bie2": "recognize",
    "ting2 zhi3": "stop",
    "kai1 shi3": "start",
    "da4 kai1 lu4 zhi4": "auto_record_on",
    "guan1 bi4 lu4 zhi4": "auto_record_off",
    "tu1 xiang4": "fusion_page",
}

# 在线识别中文关键词 -> 命令名（思路文档参考）
ONLINE_VOICE_KEYWORDS = {
    "主界面": "home",
    "主页": "home",
    "录入": "enroll",
    "开始识别": "recognize",
    "停止": "stop",
    "开始": "start",
    "打开录制": "auto_record_on",
    "关闭录制": "auto_record_off",
}

# DashScope 在线识别（阶段4思路文档用）
DASHSCOPE_API_KEY = "sk-dc4553ef7fe74c5283f05e4dc7d60adb"
ONLINE_VOICE_SAMPLE_RATE = 16000


# ==================== 状态定义 ====================
# CanMV 无 enum 模块，用整数常量类代替 IntEnum
class State:
    """系统状态枚举（整数常量）"""

    IDLE = const(0)  # 空闲
    RECOGNIZING = const(1)  # 人脸识别
    ENROLLING = const(2)  # 人脸录入
    RECORDING = const(3)  # 录制中（与识别同时）
    MANUAL_RECORDING = const(4)  # 手动纯录制
    ERROR = const(5)  # 错误


STATE_NAMES = {
    0: "空闲",
    1: "识别中",
    2: "录入中",
    3: "录制中",
    4: "纯录制",
    5: "错误",
}

# ==================== 性能参数 ====================
# 识别节流：每隔多少毫秒做一次完整检测+识别（非节流帧只更新框位置）
RECOGNIZE_DETECT_INTERVAL_MS = 800
# 状态服务器推送帧跳
STATUS_SKIP_FRAMES = 5
# 自动录制：人脸消失后延迟停止（毫秒）
AUTO_STOP_DELAY_MS = 3000

# 主循环休眠（毫秒）
IDLE_SLEEP_MS = 5
ACTIVE_SLEEP_MS = 1
