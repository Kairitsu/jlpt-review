import 'review_state.dart';

/// Schedules spaced reviews for learned JLPT sentences.
class ReviewScheduler {
  const ReviewScheduler();

  static const int minReviewStage = 0;
  static const int maxReviewStage = 6;
  static const int initialMasteryScore = 30;
  static const double minReviewWeight = 1.0;
  static const double initialReviewWeight = 1.0;

  /// Base review intervals by review stage.
  static const Map<int, Duration> baseIntervals = <int, Duration>{
    0: Duration(minutes: 10),
    1: Duration(days: 1),
    2: Duration(days: 2),
    3: Duration(days: 4),
    4: Duration(days: 7),
    5: Duration(days: 15),
    6: Duration(days: 30),
  };

  /// Creates the initial review state immediately after first learning a sentence.
  ReviewState createInitialState({DateTime? learnedAt}) {
    final DateTime now = learnedAt ?? DateTime.now();

    return ReviewState(
      masteryScore: initialMasteryScore,
      reviewWeight: initialReviewWeight,
      reviewStage: minReviewStage,
      consecutiveCorrectCount: 0,
      consecutiveWrongCount: 0,
      forceReviewTomorrow: false,
      nextReviewAt: now.add(baseIntervals[minReviewStage]!),
      updatedAt: now,
    );
  }

  /// Applies a correct review result and schedules the next review by the new stage.
  ReviewState recordCorrect(ReviewState state, {DateTime? reviewedAt}) {
    final DateTime now = reviewedAt ?? DateTime.now();
    final int nextStage = _clampStage(state.reviewStage + 1);
    final int consecutiveCorrectCount = state.consecutiveCorrectCount + 1;
    final int masteryScore = _clampMasteryScore(state.masteryScore + 10);
    final double reviewWeight = _decreaseWeightForCorrectStreak(
      state.reviewWeight,
      consecutiveCorrectCount,
    );

    return state.copyWith(
      reviewStage: nextStage,
      consecutiveCorrectCount: consecutiveCorrectCount,
      consecutiveWrongCount: 0,
      masteryScore: masteryScore,
      reviewWeight: reviewWeight,
      forceReviewTomorrow: false,
      nextReviewAt: now.add(intervalForStage(nextStage)),
      updatedAt: now,
    );
  }

  /// Applies a wrong review result and forces the item into tomorrow's review queue.
  ReviewState recordWrong(ReviewState state, {DateTime? reviewedAt}) {
    final DateTime now = reviewedAt ?? DateTime.now();
    final int nextStage = _clampStage(state.reviewStage - 1, minimum: 1);
    final int consecutiveWrongCount = state.consecutiveWrongCount + 1;
    final int masteryScore = _clampMasteryScore(state.masteryScore - 15);
    final double reviewWeight = _increaseWeightForWrongStreak(
      state.reviewWeight,
      consecutiveWrongCount,
    );

    return state.copyWith(
      reviewStage: nextStage,
      consecutiveWrongCount: consecutiveWrongCount,
      consecutiveCorrectCount: 0,
      masteryScore: masteryScore,
      reviewWeight: reviewWeight,
      forceReviewTomorrow: true,
      nextReviewAt: now.add(const Duration(days: 1)),
      updatedAt: now,
    );
  }

  /// Returns the configured base interval for [stage].
  ///
  /// Stages outside the configured range use the nearest configured interval.
  Duration intervalForStage(int stage) {
    return baseIntervals[_clampStage(stage)]!;
  }

  static int _clampStage(int stage, {int minimum = minReviewStage}) {
    return stage.clamp(minimum, maxReviewStage).toInt();
  }

  static int _clampMasteryScore(int masteryScore) {
    return masteryScore.clamp(0, 100).toInt();
  }

  static double _decreaseWeightForCorrectStreak(
    double currentWeight,
    int consecutiveCorrectCount,
  ) {
    final double decrease = consecutiveCorrectCount * 0.1;
    final double nextWeight = currentWeight - decrease;
    return nextWeight < minReviewWeight ? minReviewWeight : nextWeight;
  }

  static double _increaseWeightForWrongStreak(
    double currentWeight,
    int consecutiveWrongCount,
  ) {
    return currentWeight + consecutiveWrongCount * 0.25;
  }
}
