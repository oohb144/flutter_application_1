import 'package:dio/dio.dart';

import '../../core/config.dart';

/// 人脸库条目
class FaceEntry {
  final String name;
  final int count;
  const FaceEntry({required this.name, required this.count});

  factory FaceEntry.fromJson(Map<String, dynamic> j) => FaceEntry(
        name: (j['name'] ?? '').toString(),
        count: (j['count'] ?? 0) as int,
      );
}

/// Python 后端 HTTP 客户端（人脸库管理 + 健康检查）。
///
/// [baseUrl] 由 [SettingsProvider.backendBaseUrl] 提供，运行时随设置变化。
class BackendHttpClient {
  BackendHttpClient()
      : _dio = Dio(BaseOptions(
          connectTimeout: AppConfig.httpTimeout,
          receiveTimeout: const Duration(seconds: 10),
        ));

  final Dio _dio;

  Future<bool> health(String baseUrl) async {
    try {
      final r = await _dio.get<Map<dynamic, dynamic>>('$baseUrl/health');
      return r.data?['ok'] == true;
    } catch (_) {
      return false;
    }
  }

  Future<List<FaceEntry>> listEntries(String baseUrl) async {
    final r = await _dio.get<Map<dynamic, dynamic>>('$baseUrl/face/list');
    final list = r.data?['entries'] as List? ?? [];
    return list
        .map((e) => FaceEntry.fromJson(Map<String, dynamic>.from(e)))
        .toList();
  }

  /// 从当前 RTSP 流抓一帧录入
  Future<({bool ok, String msg})> registerFromRtsp(
    String baseUrl,
    String name,
  ) async {
    final r = await _dio.post<Map<dynamic, dynamic>>(
      '$baseUrl/face/register_from_rtsp',
      data: FormData.fromMap({'name': name}),
    );
    final d = Map<String, dynamic>.from(r.data!);
    return (ok: d['ok'] == true, msg: (d['msg'] ?? '录入成功').toString());
  }

  Future<({bool ok, String msg})> delete(String baseUrl, String name) async {
    final r = await _dio.delete<Map<dynamic, dynamic>>(
      '$baseUrl/face/$name',
    );
    final d = Map<String, dynamic>.from(r.data!);
    return (ok: d['ok'] == true, msg: d['ok'] == true ? '已删除' : '不存在');
  }

  Future<double> getThreshold(String baseUrl) async {
    final r = await _dio.get<Map<dynamic, dynamic>>('$baseUrl/face/threshold');
    return (r.data?['value'] as num?)?.toDouble() ?? AppConfig.defaultThreshold;
  }

  Future<void> setThreshold(String baseUrl, double value) async {
    await _dio.post(
      '$baseUrl/face/threshold',
      data: FormData.fromMap({'value': value}),
    );
  }

  void dispose() => _dio.close();
}
