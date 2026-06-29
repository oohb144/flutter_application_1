import 'dart:async';

import 'package:flutter/foundation.dart';

import '../../core/config.dart';
import '../models/k230_status.dart';
import '../services/k230_api_client.dart';
import 'settings_provider.dart';

/// K230 状态轮询 Provider。
///
/// 每 [AppConfig.statusPollInterval] 调一次 `GET /status`，
/// 维护在线状态、最新状态、最近错误。IP 变更后下一轮自动生效。
class K230StatusProvider extends ChangeNotifier {
  K230StatusProvider(this._settings, this._client);

  final SettingsProvider _settings;
  final K230ApiClient _client;
  Timer? _timer;
  bool _polling = false;

  bool online = false;
  K230Status? status;
  String? lastError;

  /// 启动轮询
  void start() {
    if (_timer != null) return;
    _timer = Timer.periodic(AppConfig.statusPollInterval, (_) => _poll());
    _poll(); // 立即跑一次
  }

  @override
  void dispose() {
    _timer?.cancel();
    _timer = null;
    super.dispose();
  }

  Future<void> _poll() async {
    if (_polling) return; // 避免重入
    _polling = true;
    try {
      final s = await _client.getStatus(_settings.k230BaseUrl);
      status = s;
      online = true;
      lastError = null;
    } catch (e) {
      online = false;
      lastError = e.toString();
    }
    _polling = false;
    notifyListeners();
  }

  /// 手动触发一次刷新
  Future<void> refresh() => _poll();
}
