import 'package:flutter/material.dart';

import '../../data/models/detection.dart';
import '../../utils/coord_mapper.dart';

/// 人脸检测框叠加层：在 RTSP 画面上绘制 bbox + label。
///
/// 熟人绿框，陌生人红框。坐标经 [CoordMapper] 从 640×480 映射到显示区。
class FaceOverlay extends StatelessWidget {
  const FaceOverlay({super.key, required this.boxes});

  final List<FaceBox> boxes;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final size = Size(constraints.maxWidth, constraints.maxHeight);
        return CustomPaint(
          size: size,
          painter: _OverlayPainter(boxes: boxes, displaySize: size),
        );
      },
    );
  }
}

class _OverlayPainter extends CustomPainter {
  _OverlayPainter({required this.boxes, required this.displaySize});

  final List<FaceBox> boxes;
  final Size displaySize;

  @override
  void paint(Canvas canvas, Size size) {
    if (boxes.isEmpty) return;
    final mapper = CoordMapper(displaySize);
    final boxPaint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.0;

    for (final b in boxes) {
      if (b.isEmpty) continue;
      final rect = mapper.mapRect(
        b.x1.toDouble(), b.y1.toDouble(), b.x2.toDouble(), b.y2.toDouble());
      boxPaint.color = b.known ? Colors.green : Colors.redAccent;
      canvas.drawRect(rect, boxPaint);

      // 标签背景 + 文字
      final label = '${b.label}  ${b.score.toStringAsFixed(2)}';
      final tp = TextPainter(
        text: TextSpan(
          text: label,
          style: const TextStyle(color: Colors.white, fontSize: 12),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      final labelRect = Rect.fromLTWH(
        rect.left,
        rect.top - tp.height - 2,
        tp.width + 8,
        tp.height + 2,
      );
      canvas.drawRect(
        labelRect,
        Paint()..color = (b.known ? Colors.green : Colors.redAccent).withValues(alpha: 0.6),
      );
      tp.paint(canvas, Offset(rect.left + 4, rect.top - tp.height - 1));
    }
  }

  @override
  bool shouldRepaint(covariant _OverlayPainter old) =>
      !identical(old.boxes, boxes);
}
