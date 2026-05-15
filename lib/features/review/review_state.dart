/// Persistent scheduling metadata for a sentence review item.
class ReviewState {
  const ReviewState({
    required this.masteryScore,
    required this.reviewWeight,
    required this.reviewStage,
    required this.consecutiveCorrectCount,
    required this.consecutiveWrongCount,
    required this.forceReviewTomorrow,
    required this.nextReviewAt,
    required this.updatedAt,
  });

  /// Learner mastery from 0 to 100.
  final int masteryScore;

  /// Priority multiplier used when sorting pending reviews.
  ///
  /// A value of 1.0 is the minimum/neutral priority.
  final double reviewWeight;

  /// Spaced-repetition stage used to choose the next base interval.
  final int reviewStage;

  /// Number of consecutive correct answers.
  final int consecutiveCorrectCount;

  /// Number of consecutive wrong answers.
  final int consecutiveWrongCount;

  /// Whether the item should be forced into tomorrow's review queue.
  final bool forceReviewTomorrow;

  /// The next time this item should be reviewed.
  final DateTime nextReviewAt;

  /// The last time the scheduler updated this state.
  final DateTime updatedAt;

  ReviewState copyWith({
    int? masteryScore,
    double? reviewWeight,
    int? reviewStage,
    int? consecutiveCorrectCount,
    int? consecutiveWrongCount,
    bool? forceReviewTomorrow,
    DateTime? nextReviewAt,
    DateTime? updatedAt,
  }) {
    return ReviewState(
      masteryScore: masteryScore ?? this.masteryScore,
      reviewWeight: reviewWeight ?? this.reviewWeight,
      reviewStage: reviewStage ?? this.reviewStage,
      consecutiveCorrectCount:
          consecutiveCorrectCount ?? this.consecutiveCorrectCount,
      consecutiveWrongCount: consecutiveWrongCount ?? this.consecutiveWrongCount,
      forceReviewTomorrow: forceReviewTomorrow ?? this.forceReviewTomorrow,
      nextReviewAt: nextReviewAt ?? this.nextReviewAt,
      updatedAt: updatedAt ?? this.updatedAt,
    );
  }
}
