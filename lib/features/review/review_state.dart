/// Persistent spaced-review metadata for one study sentence.
///
/// JSON keys intentionally use the existing snake_case domain names so callers
/// can map records directly to storage rows/documents.
class ReviewState {
  const ReviewState({
    required this.sentenceId,
    required this.nextReviewAt,
    this.forceReviewTomorrow = false,
    this.reviewWeight = 0,
    this.totalWrongCount = 0,
    this.totalReviewCount = 0,
    this.lastReviewedAt,
  });

  final String sentenceId;
  final DateTime nextReviewAt;
  final bool forceReviewTomorrow;
  final int reviewWeight;
  final int totalWrongCount;
  final int totalReviewCount;
  final DateTime? lastReviewedAt;

  /// Whether this sentence should be selected for a review plan ending at
  /// [todayEnd]. Forced reviews are always due, even when their scheduled date
  /// is after [todayEnd].
  bool isDueBy(DateTime todayEnd) {
    return forceReviewTomorrow || !nextReviewAt.isAfter(todayEnd);
  }

  /// Returns the updated state after this sentence has been reviewed.
  ///
  /// A completed review always clears [forceReviewTomorrow], because the forced
  /// task has been handled. The caller may pass the next spaced-review date and
  /// whether the learner was wrong during the review attempt.
  ReviewState completeReview({
    required DateTime completedAt,
    DateTime? nextReviewAt,
    bool answeredWrong = false,
    int? reviewWeight,
  }) {
    return copyWith(
      nextReviewAt: nextReviewAt ?? this.nextReviewAt,
      forceReviewTomorrow: false,
      reviewWeight: reviewWeight ?? this.reviewWeight,
      totalWrongCount: totalWrongCount + (answeredWrong ? 1 : 0),
      totalReviewCount: totalReviewCount + 1,
      lastReviewedAt: completedAt,
    );
  }

  ReviewState copyWith({
    String? sentenceId,
    DateTime? nextReviewAt,
    bool? forceReviewTomorrow,
    int? reviewWeight,
    int? totalWrongCount,
    int? totalReviewCount,
    DateTime? lastReviewedAt,
  }) {
    return ReviewState(
      sentenceId: sentenceId ?? this.sentenceId,
      nextReviewAt: nextReviewAt ?? this.nextReviewAt,
      forceReviewTomorrow: forceReviewTomorrow ?? this.forceReviewTomorrow,
      reviewWeight: reviewWeight ?? this.reviewWeight,
      totalWrongCount: totalWrongCount ?? this.totalWrongCount,
      totalReviewCount: totalReviewCount ?? this.totalReviewCount,
      lastReviewedAt: lastReviewedAt ?? this.lastReviewedAt,
    );
  }

  factory ReviewState.fromJson(Map<String, Object?> json) {
    return ReviewState(
      sentenceId: json['sentence_id']! as String,
      nextReviewAt: _readDateTime(json['next_review_at'])!,
      forceReviewTomorrow: json['force_review_tomorrow'] as bool? ?? false,
      reviewWeight: _readInt(json['review_weight']),
      totalWrongCount: _readInt(json['total_wrong_count']),
      totalReviewCount: _readInt(json['total_review_count']),
      lastReviewedAt: _readDateTime(json['last_reviewed_at']),
    );
  }

  Map<String, Object?> toJson() {
    return <String, Object?>{
      'sentence_id': sentenceId,
      'next_review_at': nextReviewAt.toIso8601String(),
      'force_review_tomorrow': forceReviewTomorrow,
      'review_weight': reviewWeight,
      'total_wrong_count': totalWrongCount,
      'total_review_count': totalReviewCount,
      'last_reviewed_at': lastReviewedAt?.toIso8601String(),
    };
  }
}

int _readInt(Object? value) {
  if (value == null) {
    return 0;
  }

  if (value is int) {
    return value;
  }

  if (value is num) {
    return value.toInt();
  }

  return int.parse(value as String);
}

DateTime? _readDateTime(Object? value) {
  if (value == null) {
    return null;
  }

  if (value is DateTime) {
    return value;
  }

  return DateTime.parse(value as String);
}
