import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/config.dart';
import '../../data/providers/settings_provider.dart';

/// 设置页：K230 IP、后端地址、识别阈值（持久化）。
class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key});

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  late final TextEditingController _k230IpCtrl;
  late final TextEditingController _backendHostCtrl;
  late final TextEditingController _backendPortCtrl;
  late double _threshold;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    final s = context.read<SettingsProvider>();
    _k230IpCtrl = TextEditingController(text: s.k230Ip);
    _backendHostCtrl = TextEditingController(text: s.backendHost);
    _backendPortCtrl = TextEditingController(text: s.backendPort.toString());
    _threshold = s.threshold;
  }

  @override
  void dispose() {
    _k230IpCtrl.dispose();
    _backendHostCtrl.dispose();
    _backendPortCtrl.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    setState(() => _saving = true);
    final s = context.read<SettingsProvider>();
    await s.setK230Ip(_k230IpCtrl.text.trim());
    await s.setBackendHost(_backendHostCtrl.text.trim());
    final port = int.tryParse(_backendPortCtrl.text.trim());
    if (port != null && port > 0 && port < 65536) {
      await s.setBackendPort(port);
    }
    await s.setThreshold(_threshold);
    setState(() => _saving = false);
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('设置已保存')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('设置')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          _sectionTitle('K230'),
          TextField(
            controller: _k230IpCtrl,
            decoration: const InputDecoration(
              labelText: 'K230 IP 地址',
              hintText: '192.168.123.183',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 8),
          Text(
            'RTSP 流：${context.watch<SettingsProvider>().rtspUrl}',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: 24),
          _sectionTitle('Python 后端'),
          TextField(
            controller: _backendHostCtrl,
            decoration: const InputDecoration(
              labelText: '后端 Host',
              hintText: '127.0.0.1',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _backendPortCtrl,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(
              labelText: '后端端口',
              hintText: '8000',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 24),
          _sectionTitle('识别'),
          Text('相似度阈值：${_threshold.toStringAsFixed(2)}'),
          Slider(
            value: _threshold,
            min: AppConfig.minThreshold,
            max: AppConfig.maxThreshold,
            divisions: 20,
            label: _threshold.toStringAsFixed(2),
            onChanged: (v) => setState(() => _threshold = v),
          ),
          const Text(
            '阈值越高越严格（0.30 宽松 / 0.40 严格）。熟人需高于阈值才匹配。',
            style: TextStyle(fontSize: 12),
          ),
          const SizedBox(height: 32),
          FilledButton.icon(
            onPressed: _saving ? null : _save,
            icon: const Icon(Icons.save),
            label: Text(_saving ? '保存中…' : '保存'),
          ),
        ],
      ),
    );
  }

  Widget _sectionTitle(String text) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Text(
          text,
          style: Theme.of(context)
              .textTheme
              .titleSmall
              ?.copyWith(fontWeight: FontWeight.bold),
        ),
      );
}
