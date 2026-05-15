import '../models/dictation_prompt.dart';
import '../models/dictation_result.dart';
import '../models/dictation_settings.dart';

/// Compares user input against a dictation prompt using deterministic MVP rules.
class DictationJudge {
  static final RegExp _endingPunctuation = RegExp(r'[。．.!！?？、,]+$');

  const DictationJudge();

  DictationResult judge({
    required DictationPrompt prompt,
    required String input,
    DictationSettings settings = const DictationSettings(),
  }) {
    final normalizedInput = normalize(input);
    final normalizedAnswer = normalize(prompt.answer);
    final candidateAnswers = <String>[
      prompt.answer,
      if (settings.allowKanaForKanji && prompt.answerKana != null)
        prompt.answerKana!,
      if (settings.checkAcceptedAnswers) ...prompt.acceptedAnswers,
    ];

    for (final candidate in candidateAnswers) {
      final normalizedCandidate = normalize(candidate);
      if (normalizedInput == normalizedCandidate) {
        return DictationResult(
          status: DictationResultStatus.correct,
          normalizedInput: normalizedInput,
          normalizedAnswer: normalizedAnswer,
          matchedAnswer: candidate,
        );
      }
    }

    return DictationResult(
      status: DictationResultStatus.wrong,
      normalizedInput: normalizedInput,
      normalizedAnswer: normalizedAnswer,
    );
  }

  /// Applies base normalization rules for dictation answers.
  String normalize(String value) {
    return value
        .replaceAll('\u3000', ' ')
        .trim()
        .replaceAll(_endingPunctuation, '')
        .trim();
  }
}
