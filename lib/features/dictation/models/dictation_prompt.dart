/// Prompt data shown by the dictation practice UI.
class DictationPrompt {
  const DictationPrompt({
    required this.id,
    required this.translationZh,
    required this.answer,
    this.answerKana,
    this.acceptedAnswers = const <String>[],
    this.hint,
    this.audioUrl,
  });

  /// Stable identifier for the prompt/question being practiced.
  final String id;

  /// Chinese translation displayed to the learner as the dictation cue.
  final String translationZh;

  /// Canonical Japanese answer expected from the learner.
  final String answer;

  /// Optional kana-only reading used when kana replacement for kanji is allowed.
  final String? answerKana;

  /// Optional alternative answers that can be accepted by settings.
  final List<String> acceptedAnswers;

  /// Optional learner-facing hint.
  final String? hint;

  /// Optional audio URL/path for playback integrations.
  final String? audioUrl;
}
