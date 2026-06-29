import 'package:flutter/material.dart';
import 'package:media_kit/media_kit.dart';
import 'package:media_kit_video/media_kit_video.dart';

/// RTSP 拉流显示（media_kit / libmpv）。
///
/// 低延迟配置：profile=low-latency、rtsp-transport=tcp、清缓存。
/// [rtspUrl] 变化（K230 IP 改动）时自动重新 open。
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
    // mpv 低延迟参数，适用于 IP 摄像头 RTSP。
    // setProperty 由 NativePlayer（player.platform）提供，桌面端可用。
    final plat = _player.platform as dynamic;
    if (plat == null) return;
    try {
      await plat.setProperty('profile', 'low-latency');
      await plat.setProperty('rtsp-transport', 'tcp');
      await plat.setProperty('demuxer-max-backward', '0');
      await plat.setProperty('demuxer-max-forward', '0');
      await plat.setProperty('cache', 'no');
    } catch (_) {
      // 部分 mpv 属性在某些平台可能不可用，忽略
    }
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
    } catch (e) {
      _error = e.toString();
    }
    if (mounted) setState(() => _loading = false);
  }

  @override
  void didUpdateWidget(covariant RtspPlayer old) {
    super.didUpdateWidget(old);
    if (old.rtspUrl != widget.rtspUrl) {
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
                child: Text('拉流错误：$_error',
                    style: const TextStyle(color: Colors.red, fontSize: 12)),
              ),
            ),
        ],
      ),
    );
  }
}
