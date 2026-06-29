/// 全局配置常量。
///
/// K230 端：HTTP 命令服务 :8001、RTSP 推流 :8554/test。
/// 电脑端：Python 后端默认 127.0.0.1:8000。
/// 运行时可被 [SettingsProvider] 的持久化值覆盖（K230 IP、后端地址、阈值）。
class AppConfig {
  AppConfig._();

  // K230 HTTP 命令服务
  static const int k230HttpPort = 8001;

  // K230 RTSP 推流（VENC link 直推原始画面，session_name=test）
  static const int k230RtspPort = 8554;
  static const String k230RtspPath = '/test';

  // K230 推流原始分辨率（bbox 坐标映射基准）
  static const int rtspFrameWidth = 640;
  static const int rtspFrameHeight = 480;

  // Python 后端默认
  static const String defaultBackendHost = '127.0.0.1';
  static const int defaultBackendPort = 8000;

  // K230 默认 IP（占位，首次启动需在设置页填入真实 IP）
  static const String defaultK230Ip = '192.168.123.183';

  // 状态轮询间隔（K230 嵌入式设备不宜频繁请求）
  static const Duration statusPollInterval = Duration(seconds: 10);

  // 人脸识别余弦相似度阈值（ArcFace 已 L2 归一化，点积即余弦）
  static const double defaultThreshold = 0.35;
  static const double minThreshold = 0.30;
  static const double maxThreshold = 0.50;

  // HTTP 超时（K230 嵌入式设备响应较慢，适当放宽）
  static const Duration httpTimeout = Duration(seconds: 10);

  /// 构造 K230 base URL，如 http://192.168.123.183:8001
  static String k230BaseUrl(String ip) => 'http://$ip:$k230HttpPort';

  /// 构造 RTSP URL，如 rtsp://192.168.123.183:8554/test
  static String rtspUrl(String ip) => 'rtsp://$ip:$k230RtspPort$k230RtspPath';

  /// 构造后端 base URL，如 http://127.0.0.1:8000
  static String backendBaseUrl(String host, int port) => 'http://$host:$port';

  /// 构造后端 WebSocket URL，如 ws://127.0.0.1:8000/ws/detections
  static String backendWsUrl(String host, int port) =>
      'ws://$host:$port/ws/detections';
}
