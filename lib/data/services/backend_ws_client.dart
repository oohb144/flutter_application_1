import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../models/detection.dart';

/// 后端 WebSocket 客户端：连 `/ws/detections`，接收人脸检测框。
///
/// 自动重连：断开后 [reconnectDelay] 重试。
class BackendWsClient {
  BackendWsClient({this.reconnectDelay = const Duration(seconds: 2)});

  final Duration reconnectDelay;
  WebSocketChannel? _channel;
  StreamSubscription? _sub;
  Timer? _reconnectTimer;
  bool _manualStop = false;
  String? _url;

  /// 检测结果回调
  void Function(List<FaceBox> boxes)? onDetection;
  void Function(bool connected)? onConnectionChange;

  void connect(String url) {
    _url = url;
    _manualStop = false;
    _doConnect();
  }

  void _doConnect() {
    final url = _url;
    if (url == null || _manualStop) return;
    try {
      _channel = WebSocketChannel.connect(Uri.parse(url));
    } catch (e) {
      _scheduleReconnect();
      return;
    }
    _sub = _channel!.stream.listen(
      (data) {
        try {
          final json = jsonDecode(data.toString()) as Map<String, dynamic>;
          final det = Detection.fromJson(json);
          onDetection?.call(det.boxes);
        } catch (e) {
          if (kDebugMode) print('[WS] parse error: $e');
        }
      },
      onError: (e) {
        if (kDebugMode) print('[WS] error: $e');
        onConnectionChange?.call(false);
        _scheduleReconnect();
      },
      onDone: () {
        onConnectionChange?.call(false);
        _scheduleReconnect();
      },
    );
    onConnectionChange?.call(true);
  }

  void _scheduleReconnect() {
    _reconnectTimer?.cancel();
    if (_manualStop) return;
    _reconnectTimer = Timer(reconnectDelay, _doConnect);
  }

  void disconnect() {
    _manualStop = true;
    _reconnectTimer?.cancel();
    _sub?.cancel();
    _channel?.sink.close();
    _sub = null;
    _channel = null;
  }
}
