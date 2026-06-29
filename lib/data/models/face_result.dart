/// K230 `POST /face_result` 请求模型。
///
/// 电脑端识别到人脸后推送，触发 K230 播报：
/// 熟人 → 「欢迎回来 {label}」，陌生人 → 「发现陌生人」。
class FaceResult {
  final String label;
  final bool known;
  final double? score;

  const FaceResult({
    required this.label,
    required this.known,
    this.score,
  });

  Map<String, dynamic> toJson() {
    final m = <String, dynamic>{
      'label': label,
      'known': known,
    };
    if (score != null) m['score'] = score;
    return m;
  }
}
