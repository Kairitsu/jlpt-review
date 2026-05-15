/// Lifecycle state for one daily study session.
enum StudySessionStatus {
  pending,
  inProgress,
  completed,
}

/// A user's planned and completed work for one study session.
class StudySession {
  const StudySession({
    required this.id,
    required this.userId,
    required this.startedAt,
    required this.plannedNewCount,
    required this.plannedReviewCount,
    this.completedNewCount = 0,
    this.completedReviewCount = 0,
    this.status = StudySessionStatus.pending,
    this.completedAt,
  });

  final String id;
  final String userId;
  final DateTime startedAt;
  final int plannedNewCount;
  final int plannedReviewCount;
  final int completedNewCount;
  final int completedReviewCount;
  final StudySessionStatus status;
  final DateTime? completedAt;

  bool get isPlanFinished =>
      completedNewCount >= plannedNewCount && completedReviewCount >= plannedReviewCount;

  StudySession markCompleted(DateTime completedTime) {
    return copyWith(
      status: StudySessionStatus.completed,
      completedAt: completedTime,
    );
  }

  StudySession copyWith({
    String? id,
    String? userId,
    DateTime? startedAt,
    int? plannedNewCount,
    int? plannedReviewCount,
    int? completedNewCount,
    int? completedReviewCount,
    StudySessionStatus? status,
    DateTime? completedAt,
  }) {
    return StudySession(
      id: id ?? this.id,
      userId: userId ?? this.userId,
      startedAt: startedAt ?? this.startedAt,
      plannedNewCount: plannedNewCount ?? this.plannedNewCount,
      plannedReviewCount: plannedReviewCount ?? this.plannedReviewCount,
      completedNewCount: completedNewCount ?? this.completedNewCount,
      completedReviewCount: completedReviewCount ?? this.completedReviewCount,
      status: status ?? this.status,
      completedAt: completedAt ?? this.completedAt,
    );
  }
}
