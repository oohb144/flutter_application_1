/// K230 `POST /command` 请求模型。
///
/// 命令清单：rtsp / speak / play_wav / led / exit。
/// 序列化为 `{"cmd": "<name>", ...参数}`，K230 端只校验是否 dict，
/// 不校验字段——但电脑端仍按文档规范发送。
class K230Command {
  final String cmd;
  final Map<String, dynamic> params;

  const K230Command._(this.cmd, this.params);

  /// 开/关 RTSP 推流
  factory K230Command.rtsp({required bool on}) =>
      K230Command._('rtsp', {'on': on});

  /// 离线 TTS 中文播报
  factory K230Command.speak({required String text}) =>
      K230Command._('speak', {'text': text});

  /// 播放 K230 本地 wav（兜底）
  factory K230Command.playWav({required String file}) =>
      K230Command._('play_wav', {'file': file});

  /// LED 颜色 [r,g,b]
  factory K230Command.led({required List<int> rgb}) =>
      K230Command._('led', {'color': rgb});

  /// 退出 K230 主程序
  factory K230Command.exit() => const K230Command._('exit', {});

  Map<String, dynamic> toJson() => {'cmd': cmd, ...params};
}
