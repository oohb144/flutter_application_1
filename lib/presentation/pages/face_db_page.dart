import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/config.dart';
import '../../data/providers/settings_provider.dart';
import '../../data/services/backend_http_client.dart';

/// 人脸库管理：列表 / 从当前画面录入 / 删除 / 阈值。
class FaceDbPage extends StatefulWidget {
  const FaceDbPage({super.key});

  @override
  State<FaceDbPage> createState() => _FaceDbPageState();
}

class _FaceDbPageState extends State<FaceDbPage> {
  List<FaceEntry> _entries = [];
  bool _loading = true;
  String? _error;
  final _nameCtrl = TextEditingController();
  late double _threshold;

  @override
  void initState() {
    super.initState();
    _threshold = context.read<SettingsProvider>().threshold;
    _refresh();
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    final s = context.read<SettingsProvider>();
    final client = context.read<BackendHttpClient>();
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      _entries = await client.listEntries(s.backendBaseUrl);
      _threshold = await client.getThreshold(s.backendBaseUrl);
    } catch (e) {
      _error = '后端不可达：$e';
    }
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _registerFromRtsp() async {
    final name = _nameCtrl.text.trim();
    if (name.isEmpty) {
      _snack('请输入标签名');
      return;
    }
    final s = context.read<SettingsProvider>();
    final client = context.read<BackendHttpClient>();
    try {
      final r = await client.registerFromRtsp(s.backendBaseUrl, name);
      _snack(r.ok ? '录入成功（${r.msg}）' : '录入失败：${r.msg}');
      if (r.ok) {
        _nameCtrl.clear();
        await _refresh();
      }
    } catch (e) {
      _snack('失败：$e');
    }
  }

  Future<void> _delete(String name) async {
    final s = context.read<SettingsProvider>();
    final client = context.read<BackendHttpClient>();
    try {
      final r = await client.delete(s.backendBaseUrl, name);
      _snack(r.msg);
      await _refresh();
    } catch (e) {
      _snack('失败：$e');
    }
  }

  Future<void> _setThreshold(double v) async {
    final s = context.read<SettingsProvider>();
    final client = context.read<BackendHttpClient>();
    try {
      await client.setThreshold(s.backendBaseUrl, v);
      await s.setThreshold(v);
    } catch (e) {
      _snack('阈值同步失败：$e');
    }
  }

  void _snack(String msg) {
    final m = ScaffoldMessenger.maybeOf(context);
    m?.showSnackBar(SnackBar(content: Text(msg)));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('人脸库'),
        actions: [
          IconButton(icon: const Icon(Icons.refresh), onPressed: _refresh),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Text(_error!, style: const TextStyle(color: Colors.red)),
                      const SizedBox(height: 8),
                      FilledButton(onPressed: _refresh, child: const Text('重试')),
                    ],
                  ),
                )
              : ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    _registerCard(),
                    const SizedBox(height: 16),
                    _thresholdCard(),
                    const SizedBox(height: 16),
                    Text('已注册 ${_entries.length} 人',
                        style: const TextStyle(fontWeight: FontWeight.bold)),
                    const SizedBox(height: 8),
                    ..._entries.map(_entryTile),
                  ],
                ),
    );
  }

  Widget _registerCard() => Card(
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('从当前画面录入',
                  style: TextStyle(fontWeight: FontWeight.bold)),
              const SizedBox(height: 8),
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _nameCtrl,
                      decoration: const InputDecoration(
                        isDense: true,
                        labelText: '标签名',
                        border: OutlineInputBorder(),
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  FilledButton.icon(
                    onPressed: _registerFromRtsp,
                    icon: const Icon(Icons.camera_alt),
                    label: const Text('录入'),
                  ),
                ],
              ),
              const SizedBox(height: 4),
              const Text('提示：面向 K230 摄像头，点录入后后端抓一帧注册。',
                  style: TextStyle(fontSize: 12)),
            ],
          ),
        ),
      );

  Widget _thresholdCard() => Card(
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('相似度阈值', style: TextStyle(fontWeight: FontWeight.bold)),
              Text('当前：${_threshold.toStringAsFixed(2)}'),
              Slider(
                value: _threshold,
                min: AppConfig.minThreshold,
                max: AppConfig.maxThreshold,
                divisions: 20,
                label: _threshold.toStringAsFixed(2),
                onChanged: (v) => setState(() => _threshold = v),
                onChangeEnd: _setThreshold,
              ),
            ],
          ),
        ),
      );

  Widget _entryTile(FaceEntry e) => ListTile(
        leading: const CircleAvatar(child: Icon(Icons.person)),
        title: Text(e.name),
        subtitle: Text('注册 ${e.count} 张'),
        trailing: IconButton(
          icon: const Icon(Icons.delete_outline, color: Colors.red),
          onPressed: () => _confirmDelete(e.name),
        ),
      );

  Future<void> _confirmDelete(String name) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('删除 $name？'),
        content: const Text('该人所有特征向量将被删除。'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Colors.red),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (ok == true) await _delete(name);
  }
}
