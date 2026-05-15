import '../models/study_record.dart';
import '../models/study_session.dart';

/// Read/write boundary used by [DailyStatsService].
///
/// Production code can implement this with SQLite, Drift, Isar, Supabase, or any
/// other repository. Keeping it abstract lets the statistics rules remain easy
/// to unit test.
abstract class DailyStatsRepository {
  Future<List<StudySession>> fetchSessions({
    required String userId,
    required DateTime from,
    required DateTime to,
  });

  Future<List<StudyRecord>> fetchRecords({
    required String userId,
    required DateTime from,
    required DateTime to,
  });

  Future<List<StudyRecord>> fetchReviewRecordsDue({
    required String userId,
    required DateTime from,
    required DateTime to,
  });

  Future<void> saveStudySession(StudySession session);
}

/// Aggregated values shown on the study completion screen.
class DailyStudyStats {
  const DailyStudyStats({
    required this.todayNewSentenceCount,
    required this.todayReviewSentenceCount,
    required this.dictationSubmissionCount,
    required this.dictationCorrectCount,
    required this.wrongSentenceCount,
    required this.tomorrowExpectedReviewCount,
    required this.studyStreakDays,
  });

  final int todayNewSentenceCount;
  final int todayReviewSentenceCount;
  final int dictationSubmissionCount;
  final int dictationCorrectCount;
  final int wrongSentenceCount;
  final int tomorrowExpectedReviewCount;
  final int studyStreakDays;

  double get dictationAccuracy {
    if (dictationSubmissionCount == 0) {
      return 0;
    }

    return dictationCorrectCount / dictationSubmissionCount;
  }

  int get dictationAccuracyPercent => (dictationAccuracy * 100).round();
}

/// Calculates daily study statistics and completes sessions when all planned
/// work has been finished.
class DailyStatsService {
  const DailyStatsService({required DailyStatsRepository repository})
      : _repository = repository;

  final DailyStatsRepository _repository;

  Future<DailyStudyStats> getTodayStats({
    required String userId,
    DateTime? now,
  }) async {
    final referenceTime = now ?? DateTime.now();
    final today = _startOfDay(referenceTime);
    final tomorrow = today.add(const Duration(days: 1));
    final dayAfterTomorrow = today.add(const Duration(days: 2));

    final todayRecords = await _repository.fetchRecords(
      userId: userId,
      from: today,
      to: tomorrow,
    );
    final tomorrowDueRecords = await _repository.fetchReviewRecordsDue(
      userId: userId,
      from: tomorrow,
      to: dayAfterTomorrow,
    );
    final completedSessions = await _repository.fetchSessions(
      userId: userId,
      from: today.subtract(const Duration(days: 3650)),
      to: tomorrow,
    );

    return calculateStats(
      todayRecords: todayRecords,
      tomorrowDueRecords: tomorrowDueRecords,
      completedSessions: completedSessions,
      now: referenceTime,
    );
  }

  DailyStudyStats calculateStats({
    required List<StudyRecord> todayRecords,
    required List<StudyRecord> tomorrowDueRecords,
    required List<StudySession> completedSessions,
    DateTime? now,
  }) {
    final referenceTime = now ?? DateTime.now();
    final today = _startOfDay(referenceTime);
    final tomorrow = today.add(const Duration(days: 1));
    final dayAfterTomorrow = today.add(const Duration(days: 2));
    final recordsForToday = todayRecords.where((record) => _isWithinHalfOpenRange(
          record.studiedAt,
          today,
          tomorrow,
        ));
    final dueTomorrow = tomorrowDueRecords.where((record) {
      final nextReviewAt = record.nextReviewAt;
      return nextReviewAt != null &&
          _isWithinHalfOpenRange(nextReviewAt, tomorrow, dayAfterTomorrow);
    });

    return DailyStudyStats(
      todayNewSentenceCount: _uniqueSentenceCount(
        recordsForToday.where((record) => record.type == StudyRecordType.newSentence),
      ),
      todayReviewSentenceCount: _uniqueSentenceCount(
        recordsForToday.where((record) => record.type == StudyRecordType.review),
      ),
      dictationSubmissionCount: recordsForToday
          .where((record) => record.hasDictationSubmission)
          .length,
      dictationCorrectCount: recordsForToday
          .where((record) => record.isDictationCorrect)
          .length,
      wrongSentenceCount: _uniqueSentenceCount(
        recordsForToday.where((record) => record.countsAsWrong),
      ),
      tomorrowExpectedReviewCount: _uniqueSentenceCount(dueTomorrow),
      studyStreakDays: _calculateStudyStreakDays(completedSessions, today),
    );
  }

  /// Marks [session] as completed and persists `study_session.status = completed`
  /// plus `completed_at` when its planned new and review work is finished.
  Future<StudySession?> completeSessionIfPlanFinished(
    StudySession session, {
    DateTime? completedAt,
  }) async {
    if (!session.isPlanFinished) {
      return null;
    }

    final completedSession = session.markCompleted(completedAt ?? DateTime.now());
    await _repository.saveStudySession(completedSession);
    return completedSession;
  }

  /// Returns true when the caller should navigate to `CompletionPage`.
  Future<bool> shouldOpenCompletionPageAfterPlanUpdate(
    StudySession session, {
    DateTime? completedAt,
  }) async {
    final completedSession = await completeSessionIfPlanFinished(
      session,
      completedAt: completedAt,
    );
    return completedSession != null;
  }

  static int _uniqueSentenceCount(Iterable<StudyRecord> records) {
    return records.map((record) => record.sentenceId).toSet().length;
  }

  static int _calculateStudyStreakDays(
    List<StudySession> sessions,
    DateTime today,
  ) {
    final completedDays = sessions
        .where((session) => session.status == StudySessionStatus.completed)
        .map((session) => _startOfDay(session.completedAt ?? session.startedAt))
        .toSet();

    var streak = 0;
    var cursor = today;
    while (completedDays.contains(cursor)) {
      streak += 1;
      cursor = cursor.subtract(const Duration(days: 1));
    }

    return streak;
  }

  static DateTime _startOfDay(DateTime value) {
    return DateTime(value.year, value.month, value.day);
  }

  static bool _isWithinHalfOpenRange(DateTime value, DateTime from, DateTime to) {
    return !value.isBefore(from) && value.isBefore(to);
  }
}
