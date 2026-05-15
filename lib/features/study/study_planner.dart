import '../../data/database/study_database.dart';

class StudyPlan {
  const StudyPlan({
    required this.session,
    required this.groups,
  });

  final StudySession session;
  final List<StudyGroup> groups;

  bool get isCompleted => session.status == StudySessionStatus.completed;

  StudyGroup? get activeGroup {
    final activeGroupId = session.activeGroupId;
    if (activeGroupId == null) return null;
    for (final group in groups) {
      if (group.id == activeGroupId) return group;
    }
    return null;
  }
}

class StudyPlanner {
  const StudyPlanner(this._database, {DateTime Function()? clock})
      : _clock = clock;

  final StudyDatabase _database;
  final DateTime Function()? _clock;

  Future<StudyPlan> planToday() => planForDate(_now());

  Future<StudyPlan> planForDate(DateTime date) async {
    var session = await _findOrCreateSession(date);
    var groups = await _database.findStudyGroupsForSession(session.id);

    if (groups.isEmpty) {
      groups = await _buildGroupsForSession(session, date);
    }

    session = await _ensureActiveGroup(session, groups);
    groups = await _database.findStudyGroupsForSession(session.id);
    return StudyPlan(session: session, groups: groups);
  }

  Future<StudyPlan> completeCurrentGroup({DateTime? date}) async {
    var plan = await planForDate(date ?? _now());
    final activeGroup = plan.activeGroup;
    if (activeGroup == null) return plan;

    await _database.updateStudyGroup(
      activeGroup.copyWith(status: StudyGroupStatus.completed),
    );

    final groups = await _database.findStudyGroupsForSession(plan.session.id);
    final nextGroup = _nextPendingGroup(groups);
    final session = nextGroup == null
        ? plan.session.copyWith(
            status: StudySessionStatus.completed,
            clearActiveGroupId: true,
          )
        : plan.session.copyWith(activeGroupId: nextGroup.id);

    await _database.updateStudySession(session);
    if (nextGroup != null) {
      await _database.updateStudyGroup(
        nextGroup.copyWith(status: StudyGroupStatus.active),
      );
    }

    return StudyPlan(
      session: session,
      groups: await _database.findStudyGroupsForSession(session.id),
    );
  }

  Future<StudySession> _findOrCreateSession(DateTime date) async {
    final studyDate = normalizeStudyDate(date);
    return await _database.findStudySessionByDate(studyDate) ??
        await _database.createStudySession(studyDate);
  }

  Future<List<StudyGroup>> _buildGroupsForSession(
    StudySession session,
    DateTime date,
  ) async {
    final settings = await _database.readStudySettings();
    final todayEnd = endOfStudyDate(date);

    final reviewSentences =
        await _database.findReviewSentencesDueBefore(todayEnd);
    reviewSentences.sort(_compareReviewPriority);

    final reviewSentenceIds =
        reviewSentences.map((sentence) => sentence.id).toSet();
    final newSentences = (await _database.findUnlearnedSentences(
      limit: settings.dailyNewSentenceCount + reviewSentenceIds.length,
    ))
        .where((sentence) => !reviewSentenceIds.contains(sentence.id))
        .take(settings.dailyNewSentenceCount)
        .toList();

    final groups = <StudyGroup>[];
    var sequence = 0;
    for (final chunk in _chunks(reviewSentences, settings.sentencesPerGroup)) {
      groups.add(
        await _database.createStudyGroup(
          sessionId: session.id,
          type: StudyGroupType.review,
          sequence: sequence++,
          sentenceIds: chunk.map((sentence) => sentence.id).toList(),
        ),
      );
    }

    for (final chunk in _chunks(newSentences, settings.sentencesPerGroup)) {
      groups.add(
        await _database.createStudyGroup(
          sessionId: session.id,
          type: StudyGroupType.newLearning,
          sequence: sequence++,
          sentenceIds: chunk.map((sentence) => sentence.id).toList(),
        ),
      );
    }

    return groups;
  }

  Future<StudySession> _ensureActiveGroup(
    StudySession session,
    List<StudyGroup> groups,
  ) async {
    if (session.status == StudySessionStatus.completed) return session;

    final activeGroupId = session.activeGroupId;
    if (activeGroupId != null) {
      final activeGroup = _findGroup(groups, activeGroupId);
      if (activeGroup != null) {
        if (activeGroup.status != StudyGroupStatus.active) {
          await _database.updateStudyGroup(
            activeGroup.copyWith(status: StudyGroupStatus.active),
          );
        }
        return session;
      }
    }

    final nextGroup = _nextPendingGroup(groups);
    if (nextGroup == null) {
      final completedSession = session.copyWith(
        status: StudySessionStatus.completed,
        clearActiveGroupId: true,
      );
      await _database.updateStudySession(completedSession);
      return completedSession;
    }

    await _database.updateStudyGroup(
      nextGroup.copyWith(status: StudyGroupStatus.active),
    );
    final updatedSession = session.copyWith(activeGroupId: nextGroup.id);
    await _database.updateStudySession(updatedSession);
    return updatedSession;
  }

  StudyGroup? _nextPendingGroup(List<StudyGroup> groups) {
    final pendingGroups = groups
        .where((group) => group.status == StudyGroupStatus.pending)
        .toList()
      ..sort((a, b) => a.sequence.compareTo(b.sequence));
    return pendingGroups.isEmpty ? null : pendingGroups.first;
  }

  StudyGroup? _findGroup(List<StudyGroup> groups, String groupId) {
    for (final group in groups) {
      if (group.id == groupId) return group;
    }
    return null;
  }

  DateTime _now() => _clock?.call() ?? DateTime.now();
}

int _compareReviewPriority(StudySentence a, StudySentence b) {
  final forced = _compareBoolDesc(
    a.forceReviewTomorrow,
    b.forceReviewTomorrow,
  );
  if (forced != 0) return forced;

  final weight = b.reviewWeight.compareTo(a.reviewWeight);
  if (weight != 0) return weight;

  final wrongCount = b.totalWrongCount.compareTo(a.totalWrongCount);
  if (wrongCount != 0) return wrongCount;

  return _compareNullableDateAsc(a.nextReviewAt, b.nextReviewAt);
}

int _compareBoolDesc(bool a, bool b) {
  if (a == b) return 0;
  return a ? -1 : 1;
}

int _compareNullableDateAsc(DateTime? a, DateTime? b) {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  return a.compareTo(b);
}

Iterable<List<T>> _chunks<T>(List<T> values, int size) sync* {
  for (var index = 0; index < values.length; index += size) {
    final end = index + size > values.length ? values.length : index + size;
    yield values.sublist(index, end);
  }
}
