import 'package:flutter/widgets.dart';

import '../models/study_session.dart';
import '../pages/completion_page.dart';
import 'daily_stats_service.dart';

/// Coordinates the end-of-plan transition from the study flow to
/// [CompletionPage].
class StudyCompletionCoordinator {
  const StudyCompletionCoordinator({required DailyStatsService statsService})
      : _statsService = statsService;

  final DailyStatsService _statsService;

  /// Completes [updatedSession] when today's plan is finished, persists
  /// `study_session.status = completed` and `completed_at`, then replaces the
  /// current route with [CompletionPage].
  Future<bool> openCompletionPageIfPlanFinished({
    required BuildContext context,
    required String userId,
    required StudySession updatedSession,
    DateTime? now,
  }) async {
    final completedSession = await _statsService.completeSessionIfPlanFinished(
      updatedSession,
      completedAt: now,
    );
    if (completedSession == null || !context.mounted) {
      return false;
    }

    final stats = await _statsService.getTodayStats(
      userId: userId,
      now: now,
    );
    if (!context.mounted) {
      return false;
    }

    await CompletionPage.open<void>(context, stats: stats);
    return true;
  }
}
