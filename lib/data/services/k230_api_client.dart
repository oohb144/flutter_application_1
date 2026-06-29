import 'package:dio/dio.dart';

import '../../core/config.dart';
import '../models/face_result.dart';
import '../models/k230_command.dart';
import '../models/k230_status.dart';

/// K230 HTTP 命令服务客户端（:8001）。
///
/// baseUrl 在运行时随 K230 IP 变化，故每个方法显式传入 [baseUrl]
/// （由 [SettingsProvider.k230BaseUrl] 提供）。
class K230ApiClient {
  K230ApiClient()
      : _dio = Dio(BaseOptions(
          connectTimeout: AppConfig.httpTimeout,
          receiveTimeout: AppConfig.httpTimeout,
          contentType: Headers.jsonContentType,
        ));

  final Dio _dio;

  /// `GET /status` —— 查询 K230 状态
  Future<K230Status> getStatus(String baseUrl) async {
    final r = await _dio.get<Map<dynamic, dynamic>>('$baseUrl/status');
    return K230Status.fromJson(Map<String, dynamic>.from(r.data!));
  }

  /// `POST /command` —— 下发命令
  Future<({bool ok, String msg})> sendCommand(
    String baseUrl,
    K230Command command,
  ) async {
    final r = await _dio.post<Map<dynamic, dynamic>>(
      '$baseUrl/command',
      data: command.toJson(),
    );
    final d = Map<String, dynamic>.from(r.data!);
    return (ok: d['ok'] == true, msg: (d['msg'] ?? '').toString());
  }

  /// `POST /face_result` —— 推送人脸识别结果，触发 K230 播报
  Future<({bool ok, String msg})> sendFaceResult(
    String baseUrl,
    FaceResult result,
  ) async {
    final r = await _dio.post<Map<dynamic, dynamic>>(
      '$baseUrl/face_result',
      data: result.toJson(),
    );
    final d = Map<String, dynamic>.from(r.data!);
    return (ok: d['ok'] == true, msg: (d['msg'] ?? '').toString());
  }

  void dispose() => _dio.close();
}
