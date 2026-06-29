import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../data/providers/backend_process_provider.dart';

/// 后端进程控制：启动/停止按钮 + 健康状态 + 日志展开。
class BackendControl extends StatefulWidget {
  const BackendControl({super.key});

  @override
  State<BackendControl> createState() => _BackendControlState();
}

class _BackendControlState extends State<BackendControl> {
  bool _logExpanded = false;

  @override
  Widget build(BuildContext context) {
    final p = context.watch<BackendProcessProvider>();
    final healthColor = p.healthy == true
        ? Colors.green
        : (p.healthy == false ? Colors.red : Colors.grey);
    final healthText = p.healthy == true
        ? '健康'
        : (p.healthy == false ? '异常' : '未知');

    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.fiber_manual_record, size: 14, color: healthColor),
                const SizedBox(width: 6),
                const Text('Python 后端',
                    style: TextStyle(fontWeight: FontWeight.bold)),
                const SizedBox(width: 8),
                Text('$healthText · ${p.running ? "运行中" : "已停"}',
                    style: Theme.of(context).textTheme.bodySmall),
                const Spacer(),
                FilledButton.tonalIcon(
                  onPressed: p.running ? p.stop : p.start,
                  icon: Icon(p.running ? Icons.stop : Icons.play_arrow),
                  label: Text(p.running ? '停止' : '启动'),
                ),
                IconButton(
                  icon: Icon(_logExpanded
                      ? Icons.expand_less
                      : Icons.expand_more),
                  tooltip: '日志',
                  onPressed: () =>
                      setState(() => _logExpanded = !_logExpanded),
                ),
              ],
            ),
            if (_logExpanded)
              Container(
                width: double.infinity,
                constraints: const BoxConstraints(maxHeight: 160),
                margin: const EdgeInsets.only(top: 8),
                padding: const EdgeInsets.all(6),
                decoration: BoxDecoration(
                  color: Colors.black87,
                  borderRadius: BorderRadius.circular(4),
                ),
                child: SingleChildScrollView(
                  reverse: true,
                  child: SelectableText(
                    p.output.isEmpty ? '（无输出）' : p.output,
                    style: const TextStyle(
                        fontFamily: 'monospace', fontSize: 11, color: Colors.greenAccent),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
