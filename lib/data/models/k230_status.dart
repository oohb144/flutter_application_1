/// K230 `GET /status` 响应模型。
///
/// 字段契约见 K230联机方案与接口.md 第二节。注意真实 K230 在
/// http_cmd_server 未集成 main.py 前，`ip` 可能为空字符串。
class K230Status {
  final String ip;
  final String rtspUrl;
  final bool rtspRunning;
  final String state;
  final bool audioBusy;

  K230Status({
    required this.ip,
    required this.rtspUrl,
    required this.rtspRunning,
    required this.state,
    required this.audioBusy,
  });

  factory K230Status.fromJson(Map<String, dynamic> json) => K230Status(
        ip: (json['ip'] ?? '').toString(),
        rtspUrl: (json['rtsp_url'] ?? '').toString(),
        rtspRunning: json['rtsp_running'] == true,
        state: (json['state'] ?? '').toString(),
        audioBusy: json['audio_busy'] == true,
      );

  Map<String, dynamic> toJson() => {
        'ip': ip,
        'rtsp_url': rtspUrl,
        'rtsp_running': rtspRunning,
        'state': state,
        'audio_busy': audioBusy,
      };
}
