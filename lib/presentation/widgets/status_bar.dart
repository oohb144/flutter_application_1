import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../data/providers/k230_status_provider.dart';

/// K230 状态栏：在线指示灯 + IP + 推流/状态/音频忙。
class StatusBar extends StatelessWidget {
  const StatusBar({super.key});

  @override
  Widget build(BuildContext context) {
    final p = context.watch<K230StatusProvider>();
    final s = p.status;
    final online = p.online;

    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        child: Row(
          children: [
            Icon(
              Icons.fiber_manual_record,
              size: 14,
              color: online ? Colors.green : Colors.red,
            ),
            const SizedBox(width: 6),
            Text(online ? '在线' : '离线',
                style: const TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(width: 16),
            Expanded(
              child: Text(
                s == null
                    ? (online ? '查询中' : 'K230 HTTP 服务未响应')
                    : 'IP ${s.ip.isEmpty ? "(空)" : s.ip} · ${s.state} · 推流 ${s.rtspRunning ? "开" : "关"} · 音频 ${s.audioBusy ? "忙" : "闲"}',
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
            IconButton(
              icon: const Icon(Icons.refresh, size: 18),
              tooltip: '刷新',
              onPressed: () => p.refresh(),
            ),
          ],
        ),
      ),
    );
  }
}
