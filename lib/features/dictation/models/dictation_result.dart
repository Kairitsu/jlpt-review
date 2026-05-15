/// MVP dictation judgement states.
enum DictationResultStatus {
  correct,
  wrong,

  /// Reserved for future LLM-assisted fuzzy judgement.
  almost,
}

/// Result returned by [DictationJudge].
class DictationResult {
  const DictationResult({
    required this.status,
    required this.normalizedInput,
    required this.normalizedAnswer,
    this.matchedAnswer,
  });

  final DictationResultStatus status;
  final String normalizedInput;
  final String normalizedAnswer;
  final String? matchedAnswer;

  bool get isCorrect => status == DictationResultStatus.correct;
}
