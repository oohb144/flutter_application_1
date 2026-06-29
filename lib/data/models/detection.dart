/// 后端 WebSocket 推送的人脸检测模型。
///
/// 后端消息：`{"boxes": [[x1,y1,x2,y2,label,known,score], ...]}`
/// bbox 坐标基于 RTSP 原始 640×480 帧。
class FaceBox {
  final int x1, y1, x2, y2;
  final String label;
  final bool known;
  final double score;

  const FaceBox({
    required this.x1,
    required this.y1,
    required this.x2,
    required this.y2,
    required this.label,
    required this.known,
    required this.score,
  });

  /// 从后端 list 元素 `[x1,y1,x2,y2,label,known,score]` 解析
  factory FaceBox.fromList(List<dynamic> l) => FaceBox(
        x1: (l[0] as num).toInt(),
        y1: (l[1] as num).toInt(),
        x2: (l[2] as num).toInt(),
        y2: (l[3] as num).toInt(),
        label: l[4]?.toString() ?? 'unknown',
        known: l[5] == true,
        score: (l[6] as num?)?.toDouble() ?? 0.0,
      );

  bool get isEmpty => x2 <= x1 || y2 <= y1;
}

class Detection {
  final List<FaceBox> boxes;
  const Detection(this.boxes);

  factory Detection.fromJson(Map<String, dynamic> json) {
    final raw = json['boxes'];
    if (raw is! List) return const Detection([]);
    return Detection(
      raw.whereType<List>().map(FaceBox.fromList).toList(),
    );
  }
}
