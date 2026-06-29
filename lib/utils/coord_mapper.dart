import 'package:flutter/material.dart';

import '../core/config.dart';

/// RTSP 原始帧坐标（640×480）→ 显示区坐标映射。
///
/// 视频 [BoxFit.contain]：等比缩放居中，可能留 letterbox 黑边。
/// [mapRect] 把原始坐标的 Rect 映射到显示区像素。
class CoordMapper {
  CoordMapper(this.displaySize)
      : _fw = AppConfig.rtspFrameWidth.toDouble(),
        _fh = AppConfig.rtspFrameHeight.toDouble();

  final Size displaySize;
  final double _fw;
  final double _fh;

  late final double _scale =
      displaySize.width / _fw < displaySize.height / _fh
          ? displaySize.width / _fw
          : displaySize.height / _fh;

  late final double _offsetX =
      (displaySize.width - _fw * _scale) / 2;
  late final double _offsetY =
      (displaySize.height - _fh * _scale) / 2;

  Offset mapPoint(double x, double y) =>
      Offset(x * _scale + _offsetX, y * _scale + _offsetY);

  Rect mapRect(double x1, double y1, double x2, double y2) {
    final tl = mapPoint(x1, y1);
    final br = mapPoint(x2, y2);
    return Rect.fromPoints(tl, br);
  }
}
