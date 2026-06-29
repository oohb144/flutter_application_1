import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../data/providers/detection_provider.dart';
import '../../data/providers/k230_status_provider.dart';
import '../../data/providers/settings_provider.dart';
import '../widgets/backend_control.dart';
import '../widgets/command_buttons.dart';
import '../widgets/face_overlay.dart';
import '../widgets/rtsp_player.dart';
import '../widgets/status_bar.dart';
import 'face_db_page.dart';
import 'settings_page.dart';

/// 主界面：RTSP 拉流显示 + 人脸框叠加 + 状态栏 + 命令按钮。
///
/// 阶段 2：集成状态栏 + 命令按钮。视频与叠加框待阶段 3/5。
class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  @override
  void initState() {
    super.initState();
    // 启动 K230 状态轮询 + 后端 WS（首帧后启动，避免 build 期 notifyListeners）
    WidgetsBinding.instance.addPostFrameCallback((_) {
      context.read<K230StatusProvider>().start();
      context.read<DetectionProvider>().start();
    });
  }

  @override
  Widget build(BuildContext context) {
    final s = context.watch<SettingsProvider>();
    return Scaffold(
      appBar: AppBar(
        title: const Text('K230 联机'),
        actions: [
          IconButton(
            icon: const Icon(Icons.people_outline),
            tooltip: '人脸库',
            onPressed: () => _gotoFaceDb(),
          ),
          IconButton(
            icon: const Icon(Icons.settings),
            tooltip: '设置',
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const SettingsPage()),
            ),
          ),
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const StatusBar(),
            const SizedBox(height: 8),
            const BackendControl(),
            const SizedBox(height: 12),
            // RTSP 拉流显示 + 人脸框叠加
            AspectRatio(
              aspectRatio: 640 / 480,
              child: Stack(
                fit: StackFit.expand,
                children: [
                  RtspPlayer(rtspUrl: s.rtspUrl),
                  FaceOverlay(
                      boxes: context.watch<DetectionProvider>().boxes),
                ],
              ),
            ),
            const SizedBox(height: 12),
            const CommandButtons(),
            const Spacer(),
          ],
        ),
      ),
    );
  }

  void _gotoFaceDb() {
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => const FaceDbPage()),
    );
  }
}
