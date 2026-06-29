import 'package:flutter/foundation.dart';

import '../models/detection.dart';
import '../services/backend_ws_client.dart';
import 'settings_provider.dart';

/// 人脸检测状态：持有后端 WS 推来的最新检测框。
///
/// 监听 [SettingsProvider] 的后端地址变化，自动重连。
class DetectionProvider extends ChangeNotifier {
  DetectionProvider(this._settings) {
    _ws.onDetection = (b) {
      _boxes = b;
      notifyListeners();
    };
    _ws.onConnectionChange = (c) {
      _connected = c;
      notifyListeners();
    };
    _settings.addListener(_onSettingsChanged);
  }

  final SettingsProvider _settings;
  final BackendWsClient _ws = BackendWsClient();
  String? _connectedUrl;

  List<FaceBox> _boxes = const [];
  List<FaceBox> get boxes => _boxes;

  bool _connected = false;
  bool get connected => _connected;

  void start() {
    _connect(_settings.backendWsUrl);
  }

  void _onSettingsChanged() {
    final url = _settings.backendWsUrl;
    if (url != _connectedUrl) {
      _connect(url);
    }
  }

  void _connect(String url) {
    _connectedUrl = url;
    _ws.connect(url);
  }

  @override
  void dispose() {
    _settings.removeListener(_onSettingsChanged);
    _ws.disconnect();
    super.dispose();
  }
}
