import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../core/config.dart';

/// 应用设置（持久化）：K230 IP、后端地址、识别阈值。
///
/// 通过 [Provider] 注入，全局可读；改值即落盘并通知监听者。
class SettingsProvider extends ChangeNotifier {
  SettingsProvider._();

  static const _kK230Ip = 'k230_ip';
  static const _kBackendHost = 'backend_host';
  static const _kBackendPort = 'backend_port';
  static const _kThreshold = 'threshold';

  late String k230Ip;
  late String backendHost;
  late int backendPort;
  late double threshold;

  static Future<SettingsProvider> load() async {
    final p = SettingsProvider._();
    final sp = await SharedPreferences.getInstance();
    p.k230Ip = sp.getString(_kK230Ip) ?? AppConfig.defaultK230Ip;
    p.backendHost =
        sp.getString(_kBackendHost) ?? AppConfig.defaultBackendHost;
    p.backendPort = sp.getInt(_kBackendPort) ?? AppConfig.defaultBackendPort;
    p.threshold = sp.getDouble(_kThreshold) ?? AppConfig.defaultThreshold;
    return p;
  }

  Future<void> setK230Ip(String ip) async {
    k230Ip = ip;
    final sp = await SharedPreferences.getInstance();
    await sp.setString(_kK230Ip, ip);
    notifyListeners();
  }

  Future<void> setBackendHost(String host) async {
    backendHost = host;
    final sp = await SharedPreferences.getInstance();
    await sp.setString(_kBackendHost, host);
    notifyListeners();
  }

  Future<void> setBackendPort(int port) async {
    backendPort = port;
    final sp = await SharedPreferences.getInstance();
    await sp.setInt(_kBackendPort, port);
    notifyListeners();
  }

  Future<void> setThreshold(double value) async {
    threshold = value;
    final sp = await SharedPreferences.getInstance();
    await sp.setDouble(_kThreshold, value);
    notifyListeners();
  }

  /// K230 base URL，如 http://192.168.123.183:8001
  String get k230BaseUrl => AppConfig.k230BaseUrl(k230Ip);

  /// RTSP URL，如 rtsp://192.168.123.183:8554/test
  String get rtspUrl => AppConfig.rtspUrl(k230Ip);

  /// 后端 base URL，如 http://127.0.0.1:8000
  String get backendBaseUrl => AppConfig.backendBaseUrl(backendHost, backendPort);

  /// 后端 WebSocket URL，如 ws://127.0.0.1:8000/ws/detections
  String get backendWsUrl => AppConfig.backendWsUrl(backendHost, backendPort);
}
