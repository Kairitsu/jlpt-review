/// Common contract and value objects for LLM-backed Japanese learning tasks.
abstract class LlmProvider {
  /// Parses a Japanese sentence into the structured representation used by the app.
  Future<ParsedSentence> parseSentence(String sentence);

  /// Judges a dictation answer against the expected and accepted answers.
  Future<JudgeResult> judgeDictation({
    required String expectedAnswer,
    required String userAnswer,
    List<String> acceptedAnswers = const [],
    String? sourceSentence,
    String? context,
  });
}

enum SentenceParseStatus { success, failed }

class ParsedSentence {
  const ParsedSentence({
    required this.sourceSentence,
    required this.kanaSentence,
    required this.annotatedSentence,
    required this.romaji,
    required this.chineseTranslation,
    required this.jlptLevel,
    required this.words,
    required this.grammarPoints,
    required this.briefExplanation,
    required this.dictationAnswer,
    required this.acceptedAnswers,
    required this.tags,
    required this.parseStatus,
    this.errorMessage,
  });

  factory ParsedSentence.failed(String sourceSentence, {String? errorMessage}) {
    return ParsedSentence(
      sourceSentence: sourceSentence,
      kanaSentence: '',
      annotatedSentence: '',
      romaji: '',
      chineseTranslation: '',
      jlptLevel: '',
      words: const [],
      grammarPoints: const [],
      briefExplanation: '',
      dictationAnswer: '',
      acceptedAnswers: const [],
      tags: const [],
      parseStatus: SentenceParseStatus.failed,
      errorMessage: errorMessage,
    );
  }

  factory ParsedSentence.fromJson(Map<String, dynamic> json) {
    return ParsedSentence(
      sourceSentence: _asString(json['source_sentence']),
      kanaSentence: _asString(json['kana_sentence']),
      annotatedSentence: _asString(json['annotated_sentence']),
      romaji: _asString(json['romaji']),
      chineseTranslation: _asString(json['chinese_translation']),
      jlptLevel: _asString(json['jlpt_level']),
      words: _asList(json['words'])
          .map((item) => WordEntry.fromJson(_asMap(item)))
          .toList(growable: false),
      grammarPoints: _asList(json['grammar_points'])
          .map((item) => GrammarPoint.fromJson(_asMap(item)))
          .toList(growable: false),
      briefExplanation: _asString(json['brief_explanation']),
      dictationAnswer: _asString(json['dictation_answer']),
      acceptedAnswers: _asList(json['accepted_answers'])
          .map(_asString)
          .where((answer) => answer.isNotEmpty)
          .toList(growable: false),
      tags: _asList(json['tags'])
          .map(_asString)
          .where((tag) => tag.isNotEmpty)
          .toList(growable: false),
      parseStatus: _parseStatus(json['parse_status']) ?? SentenceParseStatus.success,
      errorMessage: _nullableString(json['error_message']),
    );
  }

  final String sourceSentence;
  final String kanaSentence;
  final String annotatedSentence;
  final String romaji;
  final String chineseTranslation;
  final String jlptLevel;
  final List<WordEntry> words;
  final List<GrammarPoint> grammarPoints;
  final String briefExplanation;
  final String dictationAnswer;
  final List<String> acceptedAnswers;
  final List<String> tags;
  final SentenceParseStatus parseStatus;
  final String? errorMessage;

  Map<String, dynamic> toJson() => <String, dynamic>{
        'source_sentence': sourceSentence,
        'kana_sentence': kanaSentence,
        'annotated_sentence': annotatedSentence,
        'romaji': romaji,
        'chinese_translation': chineseTranslation,
        'jlpt_level': jlptLevel,
        'words': words.map((word) => word.toJson()).toList(growable: false),
        'grammar_points': grammarPoints
            .map((grammarPoint) => grammarPoint.toJson())
            .toList(growable: false),
        'brief_explanation': briefExplanation,
        'dictation_answer': dictationAnswer,
        'accepted_answers': acceptedAnswers,
        'tags': tags,
        'parse_status': parseStatus.name,
        if (errorMessage != null) 'error_message': errorMessage,
      };
}

class WordEntry {
  const WordEntry({
    required this.surface,
    required this.reading,
    required this.meaning,
    required this.partOfSpeech,
    required this.baseForm,
  });

  factory WordEntry.fromJson(Map<String, dynamic> json) => WordEntry(
        surface: _asString(json['surface']),
        reading: _asString(json['reading']),
        meaning: _asString(json['meaning']),
        partOfSpeech: _asString(json['part_of_speech']),
        baseForm: _asString(json['base_form']),
      );

  final String surface;
  final String reading;
  final String meaning;
  final String partOfSpeech;
  final String baseForm;

  Map<String, dynamic> toJson() => <String, dynamic>{
        'surface': surface,
        'reading': reading,
        'meaning': meaning,
        'part_of_speech': partOfSpeech,
        'base_form': baseForm,
      };
}

class GrammarPoint {
  const GrammarPoint({
    required this.pattern,
    required this.meaning,
    required this.explanation,
    required this.jlptLevel,
  });

  factory GrammarPoint.fromJson(Map<String, dynamic> json) => GrammarPoint(
        pattern: _asString(json['pattern']),
        meaning: _asString(json['meaning']),
        explanation: _asString(json['explanation']),
        jlptLevel: _asString(json['jlpt_level']),
      );

  final String pattern;
  final String meaning;
  final String explanation;
  final String jlptLevel;

  Map<String, dynamic> toJson() => <String, dynamic>{
        'pattern': pattern,
        'meaning': meaning,
        'explanation': explanation,
        'jlpt_level': jlptLevel,
      };
}

class JudgeResult {
  const JudgeResult({
    required this.isCorrect,
    required this.score,
    required this.feedback,
    required this.normalizedExpected,
    required this.normalizedActual,
    this.matchedAnswer,
    this.errorMessage,
  });

  factory JudgeResult.failure({String? errorMessage}) => JudgeResult(
        isCorrect: false,
        score: 0,
        feedback: 'Unable to judge the dictation answer.',
        normalizedExpected: '',
        normalizedActual: '',
        errorMessage: errorMessage,
      );

  factory JudgeResult.fromJson(Map<String, dynamic> json) => JudgeResult(
        isCorrect: _asBool(json['is_correct']),
        score: _asDouble(json['score']).clamp(0, 1).toDouble(),
        feedback: _asString(json['feedback']),
        normalizedExpected: _asString(json['normalized_expected']),
        normalizedActual: _asString(json['normalized_actual']),
        matchedAnswer: _nullableString(json['matched_answer']),
        errorMessage: _nullableString(json['error_message']),
      );

  final bool isCorrect;
  final double score;
  final String feedback;
  final String normalizedExpected;
  final String normalizedActual;
  final String? matchedAnswer;
  final String? errorMessage;

  Map<String, dynamic> toJson() => <String, dynamic>{
        'is_correct': isCorrect,
        'score': score,
        'feedback': feedback,
        'normalized_expected': normalizedExpected,
        'normalized_actual': normalizedActual,
        if (matchedAnswer != null) 'matched_answer': matchedAnswer,
        if (errorMessage != null) 'error_message': errorMessage,
      };
}

SentenceParseStatus? _parseStatus(Object? value) {
  final status = _asString(value).toLowerCase();
  return switch (status) {
    'success' || 'succeeded' || 'parsed' => SentenceParseStatus.success,
    'failed' || 'failure' || 'error' => SentenceParseStatus.failed,
    _ => null,
  };
}

String _asString(Object? value) => value == null ? '' : value.toString().trim();

String? _nullableString(Object? value) {
  final text = _asString(value);
  return text.isEmpty ? null : text;
}

bool _asBool(Object? value) {
  if (value is bool) return value;
  final text = _asString(value).toLowerCase();
  return text == 'true' || text == 'yes' || text == '1' || text == 'correct';
}

double _asDouble(Object? value) {
  if (value is num) return value.toDouble();
  return double.tryParse(_asString(value)) ?? 0;
}

List<dynamic> _asList(Object? value) => value is List ? value : const [];

Map<String, dynamic> _asMap(Object? value) {
  if (value is Map<String, dynamic>) return value;
  if (value is Map) return Map<String, dynamic>.from(value);
  return const <String, dynamic>{};
}
