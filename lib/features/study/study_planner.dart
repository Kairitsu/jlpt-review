import '../review/review_state.dart';

/// The kind of task represented by a study group.
enum StudyGroupType {
  review('review'),
  newStudy('new_study');

  const StudyGroupType(this.storageValue);

  /// Value stored in the `group_type` field for generated task groups.
  final String storageValue;
}

/// A group of sentence IDs that should be studied together.
class StudyGroup {
  const StudyGroup({
    required this.groupType,
    required this.sentenceIds,
  });

  final StudyGroupType groupType;
  final List<String> sentenceIds;

  /// Storage/API representation for the generated group's `group_type` field.
  String get groupTypeValue => groupType.storageValue;

  bool get isReview => groupType == StudyGroupType.review;

  Map<String, Object?> toJson() {
    return <String, Object?>{
      'group_type': groupTypeValue,
      'sentence_ids': sentenceIds,
    };
  }
}

/// Settings used when generating study tasks.
class StudyPlannerSettings {
  const StudyPlannerSettings({
    required this.sentencesPerGroup,
  }) : assert(sentencesPerGroup > 0, 'sentencesPerGroup must be positive');

  /// Number of sentences contained in each generated review or new-study group.
  final int sentencesPerGroup;
}

/// Builds the ordered queue of review and new-study tasks for a study session.
class StudyPlanner {
  const StudyPlanner({required this.settings});

  final StudyPlannerSettings settings;

  /// End-of-day boundary used by the review query.
  ///
  /// A review is due when `force_review_tomorrow = true` or when
  /// `next_review_at <= todayEnd`.
  DateTime todayEnd(DateTime now) {
    return DateTime(now.year, now.month, now.day, 23, 59, 59, 999, 999);
  }

  /// Selects and sorts sentences that should become review tasks.
  ///
  /// Ordering matches the product requirement:
  /// 1. forced reviews first,
  /// 2. `review_weight` descending,
  /// 3. `total_wrong_count` descending,
  /// 4. `next_review_at` ascending.
  List<ReviewState> queryReviewStates({
    required Iterable<ReviewState> states,
    required DateTime now,
  }) {
    final end = todayEnd(now);
    final dueStates = states.where((state) => state.isDueBy(end)).toList();

    dueStates.sort(_compareReviewPriority);
    return List<ReviewState>.unmodifiable(dueStates);
  }

  /// Generates review groups using the configured sentence count per group.
  /// All generated groups use `group_type = review`.
  List<StudyGroup> buildReviewGroups({
    required Iterable<ReviewState> states,
    required DateTime now,
  }) {
    final reviewSentenceIds = queryReviewStates(
      states: states,
      now: now,
    ).map((state) => state.sentenceId).toList(growable: false);

    return _chunkSentenceIds(
      sentenceIds: reviewSentenceIds,
      groupType: StudyGroupType.review,
    );
  }

  /// Generates the default task queue: review groups first, new-study groups
  /// second.
  List<StudyGroup> buildDefaultPlan({
    required Iterable<ReviewState> reviewStates,
    required Iterable<String> newSentenceIds,
    required DateTime now,
  }) {
    return <StudyGroup>[
      ...buildReviewGroups(states: reviewStates, now: now),
      ..._chunkSentenceIds(
        sentenceIds: newSentenceIds.toList(growable: false),
        groupType: StudyGroupType.newStudy,
      ),
    ];
  }

  /// Applies completion updates for the review sentences in [group].
  ///
  /// Each handled review has its [ReviewState.forceReviewTomorrow] flag cleared
  /// by [ReviewState.completeReview]. Non-review groups do not change review
  /// state.
  Map<String, ReviewState> completeReviewGroup({
    required StudyGroup group,
    required Map<String, ReviewState> statesBySentenceId,
    required DateTime completedAt,
    Map<String, DateTime> nextReviewAtBySentenceId = const <String, DateTime>{},
    Set<String> wrongSentenceIds = const <String>{},
    Map<String, int> reviewWeightBySentenceId = const <String, int>{},
  }) {
    if (!group.isReview) {
      return Map<String, ReviewState>.unmodifiable(statesBySentenceId);
    }

    final updatedStates = Map<String, ReviewState>.of(statesBySentenceId);
    for (final sentenceId in group.sentenceIds) {
      final state = updatedStates[sentenceId];
      if (state == null) {
        continue;
      }

      updatedStates[sentenceId] = state.completeReview(
        completedAt: completedAt,
        nextReviewAt: nextReviewAtBySentenceId[sentenceId],
        answeredWrong: wrongSentenceIds.contains(sentenceId),
        reviewWeight: reviewWeightBySentenceId[sentenceId],
      );
    }

    return Map<String, ReviewState>.unmodifiable(updatedStates);
  }

  int _compareReviewPriority(ReviewState left, ReviewState right) {
    final forcedComparison = _compareBoolDescending(
      left.forceReviewTomorrow,
      right.forceReviewTomorrow,
    );
    if (forcedComparison != 0) {
      return forcedComparison;
    }

    final reviewWeightComparison =
        right.reviewWeight.compareTo(left.reviewWeight);
    if (reviewWeightComparison != 0) {
      return reviewWeightComparison;
    }

    final wrongCountComparison =
        right.totalWrongCount.compareTo(left.totalWrongCount);
    if (wrongCountComparison != 0) {
      return wrongCountComparison;
    }

    return left.nextReviewAt.compareTo(right.nextReviewAt);
  }

  int _compareBoolDescending(bool left, bool right) {
    if (left == right) {
      return 0;
    }

    return left ? -1 : 1;
  }

  List<StudyGroup> _chunkSentenceIds({
    required List<String> sentenceIds,
    required StudyGroupType groupType,
  }) {
    final groups = <StudyGroup>[];
    for (
      var index = 0;
      index < sentenceIds.length;
      index += settings.sentencesPerGroup
    ) {
      final end = index + settings.sentencesPerGroup;
      groups.add(
        StudyGroup(
          groupType: groupType,
          sentenceIds: List<String>.unmodifiable(
            sentenceIds.sublist(
              index,
              end > sentenceIds.length ? sentenceIds.length : end,
            ),
          ),
        ),
      );
    }

    return List<StudyGroup>.unmodifiable(groups);
  }
}
