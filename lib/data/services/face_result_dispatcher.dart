import 'dart:async';

import '../models/detection.dart';
import '../models/face_result.dart';
import '../providers/detection_provider.dart';
import '../providers/settings_provider.dart';
import 'k230_api_client.dart';

/// 把后端识别结果回传给 K230（`POST /face_result`），触发播报。
///
/// 监听 [DetectionProvider]：检测到人脸时选一个目标（熟人优先），
/// 按节流规则 POST。同一 [label] 在 [cooldown] 内不重复发，
/// 避免 0.3s/帧的高频轰炸。
class FaceResultDispatcher {
  FaceResultDispatcher({
    required this._detection,
    required this._api,
    required this._settings,
    this.cooldown = const Duration(seconds: 5),
  }) {
    _detection.addListener(_onChanged);
  }

  final DetectionProvider _detection;
  final K230ApiClient _api;
  final SettingsProvider _settings;
  final Duration cooldown;

  String? _lastLabel;
  DateTime? _lastTime;
  bool _sending = false;

  void _onChanged() => _dispatch(_detection.boxes);

  Future<void> _dispatch(List<FaceBox> boxes) async {
    if (boxes.isEmpty) return; // 无人脸不发
    if (_sending) return;

    // 熟人优先，否则取第一个（陌生人）
    final FaceBox pick = boxes.firstWhere(
      (b) => b.known,
      orElse: () => boxes.first,
    );

    final now = DateTime.now();
    if (_lastLabel == pick.label &&
        _lastTime != null &&
        now.difference(_lastTime!) < cooldown) {
      return; // 同一目标冷却中，跳过
    }
    _lastLabel = pick.label;
    _lastTime = now;

    _sending = true;
    try {
      await _api.sendFaceResult(
        _settings.k230BaseUrl,
        FaceResult(
          label: pick.label,
          known: pick.known,
          score: pick.score,
        ),
      );
    } catch (_) {
      // K230 离线时静默；状态栏另有指示
    } finally {
      _sending = false;
    }
  }

  void dispose() {
    _detection.removeListener(_onChanged);
  }
}
