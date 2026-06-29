import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

import 'settings_provider.dart';
import '../services/backend_http_client.dart';

/// Python 后端进程管理：Flutter 子进程拉起 / 停止 + 健康检查。
///
/// 开发期也可用户手动 `python backend/main.py` 启动，此处仅作便捷。
/// Windows 上会弹出 console 窗口显示后端日志（利于调试）。
class BackendProcessProvider extends ChangeNotifier {
  BackendProcessProvider(this._settings, this._http);

  final SettingsProvider _settings;
  final BackendHttpClient _http;

  Process? _process;
  Timer? _healthTimer;
  bool _running = false;
  bool? _healthy;
  bool _disposed = false;
  final _output = StringBuffer();

  bool get running => _running;
  bool? get healthy => _healthy;
  String get output => _output.toString();

  /// 启动后端子进程
  Future<void> start() async {
    if (_running) return;
    final args = [
      'main.py',
      '--host', _settings.backendHost,
      '--port', _settings.backendPort.toString(),
      '--rtsp', _settings.rtspUrl,
      '--threshold', _settings.threshold.toString(),
    ];
    try {
      _process = await Process.start(
        'python',
        args,
        workingDirectory: 'backend',
      );
    } catch (e) {
      _append('启动失败（确认 python 在 PATH）：$e');
      _running = false;
      notifyListeners();
      return;
    }
    _running = true;
    _healthy = null;
    notifyListeners();

    _process!.stdout.transform(utf8.decoder).listen(_append);
    _process!.stderr.transform(utf8.decoder).listen(_append);
    _process!.exitCode.then((code) {
      _append('[后端退出 code=$code]');
      _running = false;
      _healthy = false;
      _healthTimer?.cancel();
      notifyListeners();
    });

    // 后端启动较慢（加载 InsightFace 模型），延迟后开始健康检查
    _healthTimer?.cancel();
    _healthTimer = Timer(const Duration(seconds: 6), _checkHealth);
  }

  Future<void> stop() async {
    _healthTimer?.cancel();
    _process?.kill(ProcessSignal.sigterm);
    _running = false;
    _healthy = false;
    notifyListeners();
  }

  Future<void> _checkHealth() async {
    if (_disposed || !_running) return;
    final ok = await _http.health(_settings.backendBaseUrl);
    if (_disposed) return;
    _healthy = ok;
    notifyListeners();
    // 健康后周期复查（每 15s）
    _healthTimer?.cancel();
    _healthTimer = Timer(const Duration(seconds: 15), _checkHealth);
  }

  void _append(String s) {
    if (_disposed) return;
    _output.write(s);
    if (_output.length > 8000) {
      final keep = _output.toString().substring(_output.length - 4000);
      _output.clear();
      _output.write(keep);
    }
    notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _healthTimer?.cancel();
    _process?.kill(ProcessSignal.sigterm);
    super.dispose();
  }
}
