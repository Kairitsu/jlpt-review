import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'tts_provider.dart';

/// Placeholder OpenAI-compatible/custom HTTP TTS provider.
///
/// The default request shape follows OpenAI's audio speech API:
/// `{ model, voice, input, response_format, speed }`. Custom-compatible
/// services can point [TtsConfig.endpoint] at their own URL and add headers via
/// [TtsConfig.extraHeaders].
class CustomTtsProvider implements TtsProvider {
  const CustomTtsProvider(this.config, {HttpClient? httpClient})
      : _httpClient = httpClient;

  final TtsConfig config;
  final HttpClient? _httpClient;

  @override
  Future<AudioResult> synthesize(String text, TtsOptions options) async {
    await config.ensureAudioCacheDirectories();

    final filePath = config.audioPathFor(options, text);
    final file = File(filePath);
    if (await file.exists() && await file.length() > 0) {
      return AudioResult(
        filePath: filePath,
        mimeType: _mimeTypeFor(options.format),
        variant: options.variant,
        provider: 'custom-http-tts',
        cached: true,
        byteLength: await file.length(),
      );
    }

    final endpoint = config.endpoint;
    if (endpoint == null) {
      throw StateError('TTS endpoint is not configured.');
    }

    final client = _httpClient ?? HttpClient();
    try {
      final request = await client.postUrl(endpoint).timeout(config.timeout);
      request.headers.contentType = ContentType.json;
      request.headers.set(
        HttpHeaders.acceptHeader,
        _mimeTypeFor(options.format),
      );
      if (config.apiKey != null && config.apiKey!.isNotEmpty) {
        request.headers.set(
          HttpHeaders.authorizationHeader,
          'Bearer ${config.apiKey}',
        );
      }
      for (final entry in config.extraHeaders.entries) {
        request.headers.set(entry.key, entry.value);
      }

      request.write(jsonEncode(<String, Object?>{
        'model': options.model ?? config.defaultModel,
        'voice': options.voice ?? config.defaultVoice,
        'input': text,
        'response_format': options.format,
        'speed': options.speed,
      }));

      final response = await request.close().timeout(config.timeout);
      final bytes = await consolidateHttpClientResponseBytes(
        response,
      ).timeout(config.timeout);

      if (response.statusCode < 200 || response.statusCode >= 300) {
        final message = utf8.decode(bytes, allowMalformed: true);
        throw HttpException(
          'TTS request failed with status ${response.statusCode}: $message',
          uri: endpoint,
        );
      }

      await file.writeAsBytes(bytes, flush: true);
      return AudioResult(
        filePath: filePath,
        mimeType: response.headers.contentType?.mimeType ??
            _mimeTypeFor(options.format),
        variant: options.variant,
        provider: 'custom-http-tts',
        byteLength: bytes.length,
      );
    } finally {
      if (_httpClient == null) {
        client.close(force: true);
      }
    }
  }

  static Future<List<int>> consolidateHttpClientResponseBytes(
    HttpClientResponse response,
  ) async {
    final builder = BytesBuilder(copy: false);
    await for (final chunk in response) {
      builder.add(chunk);
    }
    return builder.takeBytes();
  }

  static String _mimeTypeFor(String format) {
    return switch (format.toLowerCase()) {
      'aac' => 'audio/aac',
      'flac' => 'audio/flac',
      'opus' => 'audio/opus',
      'wav' => 'audio/wav',
      _ => 'audio/mpeg',
    };
  }
}
