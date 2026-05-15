/// The study activity that produced a [StudyRecord].
enum StudyRecordType {
  /// The learner saw a sentence for the first time today.
  newSentence,

  /// The learner reviewed a sentence scheduled by spaced repetition.
  review,
}

/// One sentence-level study event.
///
/// The model is intentionally persistence-agnostic so it can be built from a
/// local database row, API DTO, or test fixture without coupling the study
/// feature to a storage implementation.
class StudyRecord {
  const StudyRecord({
    required this.id,
    required this.sessionId,
    required this.sentenceId,
    required this.type,
    required this.studiedAt,
    this.dictationSubmittedAt,
    this.dictationCorrect,
    this.isWrong = false,
    this.nextReviewAt,
  });

  final String id;
  final String sessionId;
  final String sentenceId;
  final StudyRecordType type;
  final DateTime studiedAt;
  final DateTime? dictationSubmittedAt;
  final bool? dictationCorrect;
  final bool isWrong;
  final DateTime? nextReviewAt;

  bool get hasDictationSubmission => dictationSubmittedAt != null;

  bool get isDictationCorrect => hasDictationSubmission && dictationCorrect == true;

  bool get countsAsWrong => isWrong || (hasDictationSubmission && dictationCorrect == false);

  StudyRecord copyWith({
    String? id,
    String? sessionId,
    String? sentenceId,
    StudyRecordType? type,
    DateTime? studiedAt,
    DateTime? dictationSubmittedAt,
    bool? dictationCorrect,
    bool? isWrong,
    DateTime? nextReviewAt,
  }) {
    return StudyRecord(
      id: id ?? this.id,
      sessionId: sessionId ?? this.sessionId,
      sentenceId: sentenceId ?? this.sentenceId,
      type: type ?? this.type,
      studiedAt: studiedAt ?? this.studiedAt,
      dictationSubmittedAt: dictationSubmittedAt ?? this.dictationSubmittedAt,
      dictationCorrect: dictationCorrect ?? this.dictationCorrect,
      isWrong: isWrong ?? this.isWrong,
      nextReviewAt: nextReviewAt ?? this.nextReviewAt,
    );
  }
}
