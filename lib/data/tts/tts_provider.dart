import 'dart:async';
import 'dart:io';

/// Contract implemented by all text-to-speech backends.
abstract class TtsProvider {
  Future<AudioResult> synthesize(String text, TtsOptions options);
}

/// Audio speed/cache bucket used by the learning flow.
enum TtsAudioVariant {
  standard,
  slow,
}

/// Lifecycle state stored on a sentence while TTS is generated.
enum TtsStatus {
  pending,
  generated,
  failed,
}

extension TtsStatusValue on TtsStatus {
  String get value => switch (this) {
        TtsStatus.pending => 'pending',
        TtsStatus.generated => 'generated',
        TtsStatus.failed => 'failed',
      };
}

/// Request options for a synthesis call.
class TtsOptions {
  const TtsOptions({
    this.model,
    this.voice,
    this.format = 'mp3',
    this.speed = 1.0,
    this.variant = TtsAudioVariant.standard,
    this.cacheKey,
    this.languageCode = 'ja-JP',
    this.metadata = const <String, Object?>{},
  });

  final String? model;
  final String? voice;
  final String format;
  final double speed;
  final TtsAudioVariant variant;
  final String? cacheKey;
  final String languageCode;
  final Map<String, Object?> metadata;

  TtsOptions copyWith({
    String? model,
    String? voice,
    String? format,
    double? speed,
    TtsAudioVariant? variant,
    String? cacheKey,
    String? languageCode,
    Map<String, Object?>? metadata,
  }) {
    return TtsOptions(
      model: model ?? this.model,
      voice: voice ?? this.voice,
      format: format ?? this.format,
      speed: speed ?? this.speed,
      variant: variant ?? this.variant,
      cacheKey: cacheKey ?? this.cacheKey,
      languageCode: languageCode ?? this.languageCode,
      metadata: metadata ?? this.metadata,
    );
  }
}

/// Result returned after audio has been written to the local cache.
class AudioResult {
  const AudioResult({
    required this.filePath,
    required this.mimeType,
    required this.variant,
    this.provider,
    this.cached = false,
    this.byteLength,
  });

  final String filePath;
  final String mimeType;
  final TtsAudioVariant variant;
  final String? provider;
  final bool cached;
  final int? byteLength;
}

/// Runtime configuration shared by TTS providers.
class TtsConfig {
  const TtsConfig({
    required this.documentsDirectory,
    this.endpoint,
    this.apiKey,
    this.defaultModel = 'tts-1',
    this.defaultVoice = 'alloy',
    this.timeout = const Duration(seconds: 30),
    this.extraHeaders = const <String, String>{},
  });

  /// Application documents directory. Audio is cached below [audioDirectory].
  final Directory documentsDirectory;
  final Uri? endpoint;
  final String? apiKey;
  final String defaultModel;
  final String defaultVoice;
  final Duration timeout;
  final Map<String, String> extraHeaders;

  Directory get audioDirectory {
    return Directory('${documentsDirectory.path}/audio');
  }
  Directory get standardAudioDirectory {
    return Directory('${audioDirectory.path}/standard');
  }
  Directory get slowAudioDirectory {
    return Directory('${audioDirectory.path}/slow');
  }

  Directory cacheDirectoryFor(TtsAudioVariant variant) {
    return switch (variant) {
      TtsAudioVariant.standard => standardAudioDirectory,
      TtsAudioVariant.slow => slowAudioDirectory,
    };
  }

  Future<void> ensureAudioCacheDirectories() async {
    await standardAudioDirectory.create(recursive: true);
    await slowAudioDirectory.create(recursive: true);
  }

  String audioPathFor(TtsOptions options, String text) {
    final key = options.cacheKey ?? _stableFileName(text);
    final extension = _sanitizeExtension(options.format);
    return '${cacheDirectoryFor(options.variant).path}/$key.$extension';
  }

  static String _sanitizeExtension(String format) {
    final sanitized = format
        .toLowerCase()
        .replaceAll(RegExp(r'[^a-z0-9]'), '');
    return sanitized.isEmpty ? 'mp3' : sanitized;
  }

  static String _stableFileName(String text) {
    var hash = 0x811c9dc5;
    for (final codeUnit in text.codeUnits) {
      hash ^= codeUnit;
      hash = (hash * 0x01000193) & 0xffffffff;
    }
    return hash.toRadixString(16).padLeft(8, '0');
  }
}

/// Updates sentence audio fields while keeping failures non-blocking.
class TtsSentenceAudioUpdater {
  const TtsSentenceAudioUpdater(this.provider);

  final TtsProvider provider;

  /// Best-effort generation for a sentence-like object.
  ///
  /// [sentence] is intentionally dynamic so this adapter can work with the app's
  /// database/model objects without forcing a shared base class. On success it
  /// writes `audio_path` or `slow_audio_path` and sets `tts_status` to
  /// `generated`. On any synthesis error it only sets `tts_status` to `failed`
  /// and returns null so learning can continue uninterrupted.
  Future<AudioResult?> synthesizeForSentence(
    dynamic sentence,
    String text,
    TtsOptions options,
  ) async {
    try {
      final result = await provider.synthesize(text, options);
      if (options.variant == TtsAudioVariant.slow) {
        sentence.slow_audio_path = result.filePath;
      } else {
        sentence.audio_path = result.filePath;
      }
      sentence.tts_status = TtsStatus.generated.value;
      return result;
    } catch (_) {
      sentence.tts_status = TtsStatus.failed.value;
      return null;
    }
  }
}
