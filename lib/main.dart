import 'package:flutter/material.dart';
import 'package:media_kit/media_kit.dart';
import 'package:provider/provider.dart';

import 'app.dart';
import 'data/providers/backend_process_provider.dart';
import 'data/providers/detection_provider.dart';
import 'data/providers/k230_status_provider.dart';
import 'data/providers/settings_provider.dart';
import 'data/services/backend_http_client.dart';
import 'data/services/face_result_dispatcher.dart';
import 'data/services/k230_api_client.dart';

void main() {
  // media_kit 需在 runApp 前初始化（Windows 桌面捆绑 libmpv）
  WidgetsFlutterBinding.ensureInitialized();
  MediaKit.ensureInitialized();

  // 加载持久化设置后再启动 UI
  SettingsProvider.load().then((settings) {
    final apiClient = K230ApiClient();
    final backendClient = BackendHttpClient();
    final statusProvider = K230StatusProvider(settings, apiClient);
    final detectionProvider = DetectionProvider(settings);
    final backendProcess = BackendProcessProvider(settings, backendClient);
    // 识别到人脸后自动 POST /face_result 给 K230（节流）
    final dispatcher = FaceResultDispatcher(
      detection: detectionProvider,
      api: apiClient,
      settings: settings,
    );
    runApp(
      MultiProvider(
        providers: [
          ChangeNotifierProvider<SettingsProvider>.value(value: settings),
          Provider<K230ApiClient>.value(value: apiClient),
          Provider<BackendHttpClient>.value(value: backendClient),
          ChangeNotifierProvider<K230StatusProvider>.value(
              value: statusProvider),
          ChangeNotifierProvider<DetectionProvider>.value(
              value: detectionProvider),
          ChangeNotifierProvider<BackendProcessProvider>.value(
              value: backendProcess),
          Provider<FaceResultDispatcher>.value(value: dispatcher),
        ],
        child: const K230App(),
      ),
    );
  });
}
