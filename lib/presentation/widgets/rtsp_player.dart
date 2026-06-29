import 'package:flutter/material.dart';
import 'package:media_kit/media_kit.dart';
import 'package:media_kit_video/media_kit_video.dart';

/// RTSP 拉流显示（media_kit / libmpv）。
///
/// 低延迟配置：profile=low-latency、rtsp-transport=tcp、清缓存。
/// [rtspUrl] 变化（K230 IP 改动）时自动重新 open。
/// 连接失败后自动重试（最多 5 次，间隔递增）。
class RtspPlayer extends StatefulWidget {
  final String rtspUrl;

  const RtspPlayer({super.key, required this.rtspUrl});

  @override
  State<RtspPlayer> createState() => _RtspPlayerState();
}

class _RtspPlayerState extends State<RtspPlayer> {
  late final Player _player = Player();
  late final VideoController _controller = VideoController(_player);

  String? _lastUrl;
  String? _error;
  bool _loading = true;
  int _retryCount = 0;
  static const int _maxRetries = 5;

  @override
  void initState() {
    super.initState();
    _init();
  }

  Future<void> _init() async {
    await _applyLowLatency();
    await _open(widget.rtspUrl);
  }

  Future<void> _applyLowLatency() async {
    final plat = _player.platform as dynamic;
    if (plat == null) return;
    try {
      await plat.setProperty('rtsp-transport', 'tcp');
      await plat.setProperty('rtsp-timeout', '15');
    } catch (_) {}
  }

  Future<void> _open(String url) async {
    if (url.isEmpty || url == _lastUrl) {
      setState(() => _loading = false);
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      await _player.open(Media(url));
      _lastUrl = url;
      _retryCount = 0;
    } catch (e) {
      _error = e.toString();
      _retryWithDelay();
    }
    if (mounted) setState(() => _loading = false);
  }

  void _retryWithDelay() {
    if (_retryCount >= _maxRetries || !mounted) return;
    _retryCount++;
    final delay = Duration(seconds: 2 * _retryCount);
    Future.delayed(delay, () {
      if (!mounted || _lastUrl == widget.rtspUrl) return;
      _open(widget.rtspUrl);
    });
  }

  @override
  void didUpdateWidget(covariant RtspPlayer old) {
    super.didUpdateWidget(old);
    if (old.rtspUrl != widget.rtspUrl) {
      _retryCount = 0;
      _open(widget.rtspUrl);
    }
  }

  @override
  void dispose() {
    _player.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      color: Colors.black,
      child: Stack(
        fit: StackFit.expand,
        children: [
          Video(controller: _controller, fit: BoxFit.contain),
          if (_loading)
            const Center(child: CircularProgressIndicator(color: Colors.white70)),
          if (_error != null)
            Positioned(
              left: 8,
              bottom: 8,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                color: Colors.black54,
                child: Text('拉流错误：$_error（重试 $_retryCount/${_maxRetries}）',
                    style: const TextStyle(color: Colors.red, fontSize: 12)),
              ),
            ),
        ],
      ),
    );
  }
}
