import 'dart:async';
import 'dart:convert';

import 'package:dio/dio.dart';

import 'api_config_repository.dart';
import 'llm_provider.dart';

class OpenAiCompatibleLlmProvider implements LlmProvider {
  OpenAiCompatibleLlmProvider({
    required this.configRepository,
    required this.apiKeyDecrypter,
    Dio? dio,
  }) : _dio = dio ?? Dio();

  final ApiConfigRepository configRepository;
  final ApiKeyDecrypter apiKeyDecrypter;
  final Dio _dio;

  static const _sentenceParserSystemPrompt = '''
You are a Japanese JLPT tutoring data parser. Return only valid JSON.
Do not include Markdown, comments, or explanatory prose outside the JSON object.
The JSON object must contain exactly these top-level fields:
source_sentence, kana_sentence, annotated_sentence, romaji, chinese_translation,
jlpt_level, words, grammar_points, brief_explanation, dictation_answer,
accepted_answers, tags.

Field rules:
- source_sentence: original Japanese sentence.
- kana_sentence: full sentence in kana reading.
- annotated_sentence: Japanese sentence annotated with concise furigana/reading hints.
- romaji: Hepburn-style romanization.
- chinese_translation: Simplified Chinese translation.
- jlpt_level: best estimate from N5, N4, N3, N2, N1, or unknown.
- words: array of objects with surface, reading, meaning, part_of_speech, base_form.
- grammar_points: array of objects with pattern, meaning, explanation, jlpt_level.
- brief_explanation: concise Chinese explanation for learners.
- dictation_answer: canonical answer for dictation.
- accepted_answers: array of acceptable answer variants.
- tags: array of short tags such as jlpt level, grammar, topic, or vocabulary.
Use empty strings or empty arrays when uncertain, but keep valid JSON.
''';

  static const _dictationJudgeSystemPrompt = '''
You are judging Japanese dictation for a JLPT tutor. Return only valid JSON.
The JSON object must contain: is_correct, score, feedback, normalized_expected,
normalized_actual, matched_answer.
score must be a number from 0 to 1. feedback must be concise Simplified Chinese.
Accept semantically identical Japanese answers, kana/kanji variants, punctuation
and whitespace differences, and listed accepted answers.
''';

  @override
  Future<ParsedSentence> parseSentence(String sentence) async {
    try {
      final content = await _chatCompletion(
        systemPrompt: _sentenceParserSystemPrompt,
        userPrompt: 'Parse this Japanese sentence as JSON: $sentence',
      );
      final jsonObject = _decodeJsonObject(content);
      final parsed = ParsedSentence.fromJson(jsonObject);

      return ParsedSentence(
        sourceSentence: parsed.sourceSentence.isEmpty ? sentence : parsed.sourceSentence,
        kanaSentence: parsed.kanaSentence,
        annotatedSentence: parsed.annotatedSentence,
        romaji: parsed.romaji,
        chineseTranslation: parsed.chineseTranslation,
        jlptLevel: parsed.jlptLevel,
        words: parsed.words,
        grammarPoints: parsed.grammarPoints,
        briefExplanation: parsed.briefExplanation,
        dictationAnswer: parsed.dictationAnswer,
        acceptedAnswers: parsed.acceptedAnswers,
        tags: parsed.tags,
        parseStatus: SentenceParseStatus.success,
      );
    } on Object catch (error) {
      return ParsedSentence.failed(sentence, errorMessage: _safeErrorMessage(error));
    }
  }

  @override
  Future<JudgeResult> judgeDictation({
    required String expectedAnswer,
    required String userAnswer,
    List<String> acceptedAnswers = const [],
    String? sourceSentence,
    String? context,
  }) async {
    try {
      final payload = jsonEncode(<String, dynamic>{
        'expected_answer': expectedAnswer,
        'user_answer': userAnswer,
        'accepted_answers': acceptedAnswers,
        if (sourceSentence != null) 'source_sentence': sourceSentence,
        if (context != null) 'context': context,
      });
      final content = await _chatCompletion(
        systemPrompt: _dictationJudgeSystemPrompt,
        userPrompt: 'Judge this dictation answer: $payload',
      );
      return JudgeResult.fromJson(_decodeJsonObject(content));
    } on Object catch (error) {
      return JudgeResult.failure(errorMessage: _safeErrorMessage(error));
    }
  }

  Future<String> _chatCompletion({
    required String systemPrompt,
    required String userPrompt,
  }) async {
    final config = await configRepository.getConfig();
    final apiKey = await apiKeyDecrypter.decrypt(config.encryptedApiKey);
    final timeout = Duration(seconds: config.timeoutSeconds);
    final endpoint = _chatCompletionsEndpoint(config.baseUrl);

    try {
      final response = await _dio.post<dynamic>(
        endpoint,
        options: Options(
          sendTimeout: timeout,
          receiveTimeout: timeout,
          headers: <String, String>{
            'Authorization': 'Bearer $apiKey',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-LLM-Provider': config.providerName,
          },
        ),
        data: <String, dynamic>{
          'model': config.modelName,
          'temperature': config.temperature,
          'max_tokens': config.maxTokens,
          'response_format': <String, String>{'type': 'json_object'},
          'messages': <Map<String, String>>[
            <String, String>{'role': 'system', 'content': systemPrompt},
            <String, String>{'role': 'user', 'content': userPrompt},
          ],
        },
      ).timeout(timeout);
      return _extractMessageContent(response.data);
    } on DioException catch (error) {
      throw LlmProviderException(_sanitizeDioException(error));
    } on TimeoutException {
      throw const LlmProviderException('LLM request timed out.');
    } finally {
      // Ensure the decrypted key is only held for the lifetime of this request.
    }
  }

  String _extractMessageContent(Object? data) {
    final root = _asJsonMap(data);
    final choices = root['choices'];
    if (choices is! List || choices.isEmpty) {
      throw const LlmProviderException('LLM response did not include choices.');
    }

    final firstChoice = _asJsonMap(choices.first);
    final message = _asJsonMap(firstChoice['message']);
    final content = message['content'];
    if (content is String && content.trim().isNotEmpty) return content;

    throw const LlmProviderException('LLM response did not include message content.');
  }

  Map<String, dynamic> _decodeJsonObject(String content) {
    final trimmed = _stripCodeFence(content.trim());
    final decoded = _tryDecode(trimmed) ?? _tryDecode(_extractBalancedJsonObject(trimmed));
    if (decoded == null) {
      throw const LlmProviderException('LLM response was not valid JSON.');
    }
    if (decoded is Map<String, dynamic>) return decoded;
    if (decoded is Map) return Map<String, dynamic>.from(decoded);
    throw const LlmProviderException('LLM response JSON was not an object.');
  }

  Object? _tryDecode(String value) {
    if (value.isEmpty) return null;
    try {
      return jsonDecode(value);
    } on FormatException {
      return null;
    }
  }

  String _extractBalancedJsonObject(String value) {
    final start = value.indexOf('{');
    if (start < 0) return '';

    var depth = 0;
    var inString = false;
    var escaping = false;
    for (var i = start; i < value.length; i++) {
      final char = value[i];
      if (escaping) {
        escaping = false;
        continue;
      }
      if (char == '\\' && inString) {
        escaping = true;
        continue;
      }
      if (char == '"') {
        inString = !inString;
        continue;
      }
      if (inString) continue;
      if (char == '{') depth++;
      if (char == '}') depth--;
      if (depth == 0) return value.substring(start, i + 1);
    }
    return '';
  }

  String _stripCodeFence(String value) {
    if (!value.startsWith('```')) return value;
    final withoutOpeningFence = value.replaceFirst(RegExp(r'^```(?:json)?\s*'), '');
    return withoutOpeningFence.replaceFirst(RegExp(r'\s*```$'), '').trim();
  }

  String _chatCompletionsEndpoint(String baseUrl) {
    final normalized = baseUrl.trim().replaceFirst(RegExp(r'/+$'), '');
    if (normalized.endsWith('/v1/chat/completions')) return normalized;
    if (normalized.endsWith('/v1')) return '$normalized/chat/completions';
    return '$normalized/v1/chat/completions';
  }

  Map<String, dynamic> _asJsonMap(Object? value) {
    if (value is String) return _decodeJsonObject(value);
    if (value is Map<String, dynamic>) return value;
    if (value is Map) return Map<String, dynamic>.from(value);
    throw const LlmProviderException('LLM response shape was invalid.');
  }

  String _sanitizeDioException(DioException error) {
    final statusCode = error.response?.statusCode;
    final status = statusCode == null ? '' : ' (HTTP $statusCode)';
    final type = error.type.name;
    return 'LLM request failed$status: $type.';
  }

  String _safeErrorMessage(Object error) {
    if (error is LlmProviderException) return error.message;
    return 'LLM operation failed.';
  }
}

class LlmProviderException implements Exception {
  const LlmProviderException(this.message);

  final String message;

  @override
  String toString() => message;
}
