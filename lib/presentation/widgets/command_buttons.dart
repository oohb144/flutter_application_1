import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../data/models/k230_command.dart';
import '../../data/providers/k230_status_provider.dart';
import '../../data/providers/settings_provider.dart';
import '../../data/services/k230_api_client.dart';

/// K230 命令按钮组：开/关推流、发播报、LED、退出。
class CommandButtons extends StatelessWidget {
  const CommandButtons({super.key});

  @override
  Widget build(BuildContext context) {
    final p = context.watch<K230StatusProvider>();
    final running = p.status?.rtspRunning ?? false;

    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        FilledButton.tonalIcon(
          icon: Icon(running ? Icons.videocam_off : Icons.videocam),
          label: Text(running ? '关推流' : '开推流'),
          onPressed: () => _send(context, K230Command.rtsp(on: !running)),
        ),
        FilledButton.tonalIcon(
          icon: const Icon(Icons.record_voice_over),
          label: const Text('发播报'),
          onPressed: () => _speakDialog(context),
        ),
        FilledButton.tonalIcon(
          icon: const Icon(Icons.lightbulb),
          label: const Text('LED'),
          onPressed: () => _ledDialog(context),
        ),
        OutlinedButton.icon(
          style: OutlinedButton.styleFrom(foregroundColor: Colors.red),
          icon: const Icon(Icons.power_settings_new),
          label: const Text('退出 K230'),
          onPressed: () => _exitDialog(context),
        ),
      ],
    );
  }

  Future<void> _send(BuildContext context, K230Command cmd) async {
    final client = context.read<K230ApiClient>();
    final baseUrl = context.read<SettingsProvider>().k230BaseUrl;
    try {
      final r = await client.sendCommand(baseUrl, cmd);
      if (!context.mounted) return;
      _snack(context, 'ok=${r.ok} ${r.msg}');
    } catch (e) {
      if (!context.mounted) return;
      _snack(context, '失败：$e');
    }
  }

  Future<void> _speakDialog(BuildContext context) async {
    final ctrl = TextEditingController(text: '欢迎回来');
    final text = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('发播报'),
        content: TextField(
          controller: ctrl,
          decoration: const InputDecoration(hintText: '播报文本'),
          autofocus: true,
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, ctrl.text.trim()),
            child: const Text('发送'),
          ),
        ],
      ),
    );
    if (!context.mounted) return;
    if (text != null && text.isNotEmpty) {
      await _send(context, K230Command.speak(text: text));
    }
  }

  Future<void> _ledDialog(BuildContext context) async {
    const colors = <(String, List<int>)>[
      ('红', [255, 0, 0]),
      ('绿', [0, 255, 0]),
      ('蓝', [0, 0, 255]),
      ('白', [255, 255, 255]),
      ('关', [0, 0, 0]),
    ];
    final rgb = await showDialog<List<int>>(
      context: context,
      builder: (ctx) => SimpleDialog(
        title: const Text('LED 颜色'),
        children: colors
            .map((c) => SimpleDialogOption(
                  onPressed: () => Navigator.pop(ctx, c.$2),
                  child: Text(c.$1),
                ))
            .toList(),
      ),
    );
    if (!context.mounted) return;
    if (rgb != null) {
      await _send(context, K230Command.led(rgb: rgb));
    }
  }

  Future<void> _exitDialog(BuildContext context) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('退出 K230 主程序'),
        content: const Text('确定要退出 K230 端主程序吗？'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Colors.red),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('退出'),
          ),
        ],
      ),
    );
    if (!context.mounted) return;
    if (ok == true) {
      await _send(context, K230Command.exit());
    }
  }

  void _snack(BuildContext context, String msg) {
    final m = ScaffoldMessenger.maybeOf(context);
    m?.showSnackBar(SnackBar(content: Text(msg)));
  }
}
