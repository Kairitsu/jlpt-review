/// Runtime settings that influence dictation judging.
class DictationSettings {
  const DictationSettings({
    this.allowKanaForKanji = false,
    this.checkAcceptedAnswers = true,
  });

  /// Whether kana-only input may match a canonical answer containing kanji.
  final bool allowKanaForKanji;

  /// Whether configured accepted answers are checked in addition to the canonical answer.
  final bool checkAcceptedAnswers;
}
