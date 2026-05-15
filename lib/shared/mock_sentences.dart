enum JlptLevel { n5, n4, n3, n2, n1 }

enum ParseStatus { parsed, pending, failed }

class SentenceItem {
  const SentenceItem({
    required this.japanese,
    required this.reading,
    required this.translation,
    required this.level,
    required this.status,
    required this.tags,
  });

  final String japanese;
  final String reading;
  final String translation;
  final JlptLevel level;
  final ParseStatus status;
  final List<String> tags;
}

const importedSentences = [
  SentenceItem(
    japanese: '明日は雨が降るかもしれません。',
    reading: 'あしたは あめが ふるかもしれません。',
    translation: 'Tomorrow it might rain.',
    level: JlptLevel.n4,
    status: ParseStatus.parsed,
    tags: ['weather', 'maybe'],
  ),
  SentenceItem(
    japanese: '会議に遅れないように、早めに出発します。',
    reading: 'かいぎに おくれないように、はやめに しゅっぱつします。',
    translation: 'I will leave early so that I am not late for the meeting.',
    level: JlptLevel.n3,
    status: ParseStatus.pending,
    tags: ['work', 'purpose'],
  ),
  SentenceItem(
    japanese: '努力したにもかかわらず、結果は思わしくなかった。',
    reading: 'どりょくしたにもかかわらず、けっかは おもわしくなかった。',
    translation: 'Although I worked hard, the result was not satisfactory.',
    level: JlptLevel.n2,
    status: ParseStatus.parsed,
    tags: ['contrast', 'exam'],
  ),
  SentenceItem(
    japanese: 'この表現は文脈によって意味が変わります。',
    reading: 'このひょうげんは ぶんみゃくによって いみが かわります。',
    translation: 'The meaning of this expression changes depending on context.',
    level: JlptLevel.n3,
    status: ParseStatus.failed,
    tags: ['context', 'grammar'],
  ),
];

extension JlptLevelLabel on JlptLevel {
  String get label => switch (this) {
        JlptLevel.n5 => 'N5',
        JlptLevel.n4 => 'N4',
        JlptLevel.n3 => 'N3',
        JlptLevel.n2 => 'N2',
        JlptLevel.n1 => 'N1',
      };
}

extension ParseStatusLabel on ParseStatus {
  String get label => switch (this) {
        ParseStatus.parsed => '已解析',
        ParseStatus.pending => '解析中',
        ParseStatus.failed => '需重试',
      };
}
