/// Data models and persistence contracts used by the study planner.
///
/// The interfaces in this file are intentionally storage-agnostic so they can be
/// implemented by Drift, SQLite, REST-backed repositories, or the included
/// in-memory implementation used by tests and prototypes.

enum StudyGroupType { review, newLearning }

enum StudyGroupStatus { pending, active, completed }

enum StudySessionStatus { inProgress, completed }

DateTime normalizeStudyDate(DateTime value) =>
    DateTime(value.year, value.month, value.day);

DateTime endOfStudyDate(DateTime value) {
  final day = normalizeStudyDate(value);
  return day
      .add(const Duration(days: 1))
      .subtract(const Duration(microseconds: 1));
}

class StudySettings {
  const StudySettings({
    required this.dailyNewSentenceCount,
    required this.sentencesPerGroup,
  })  : assert(dailyNewSentenceCount >= 0),
        assert(sentencesPerGroup > 0);

  final int dailyNewSentenceCount;
  final int sentencesPerGroup;
}

class StudySentence {
  const StudySentence({
    required this.id,
    this.nextReviewAt,
    this.forceReviewTomorrow = false,
    this.reviewWeight = 0,
    this.totalWrongCount = 0,
    this.studiedAt,
  });

  final String id;
  final DateTime? nextReviewAt;
  final bool forceReviewTomorrow;
  final int reviewWeight;
  final int totalWrongCount;
  final DateTime? studiedAt;

  bool get hasBeenStudied => studiedAt != null;

  StudySentence copyWith({
    DateTime? nextReviewAt,
    bool? forceReviewTomorrow,
    int? reviewWeight,
    int? totalWrongCount,
    DateTime? studiedAt,
  }) {
    return StudySentence(
      id: id,
      nextReviewAt: nextReviewAt ?? this.nextReviewAt,
      forceReviewTomorrow: forceReviewTomorrow ?? this.forceReviewTomorrow,
      reviewWeight: reviewWeight ?? this.reviewWeight,
      totalWrongCount: totalWrongCount ?? this.totalWrongCount,
      studiedAt: studiedAt ?? this.studiedAt,
    );
  }
}

class StudySession {
  const StudySession({
    required this.id,
    required this.studyDate,
    this.status = StudySessionStatus.inProgress,
    this.activeGroupId,
  });

  final String id;
  final DateTime studyDate;
  final StudySessionStatus status;
  final String? activeGroupId;

  StudySession copyWith({
    StudySessionStatus? status,
    String? activeGroupId,
    bool clearActiveGroupId = false,
  }) {
    return StudySession(
      id: id,
      studyDate: studyDate,
      status: status ?? this.status,
      activeGroupId:
          clearActiveGroupId ? null : (activeGroupId ?? this.activeGroupId),
    );
  }
}

class StudyGroup {
  const StudyGroup({
    required this.id,
    required this.sessionId,
    required this.type,
    required this.sequence,
    this.status = StudyGroupStatus.pending,
    this.sentenceIds = const <String>[],
  });

  final String id;
  final String sessionId;
  final StudyGroupType type;
  final int sequence;
  final StudyGroupStatus status;
  final List<String> sentenceIds;

  StudyGroup copyWith({
    StudyGroupStatus? status,
    List<String>? sentenceIds,
  }) {
    return StudyGroup(
      id: id,
      sessionId: sessionId,
      type: type,
      sequence: sequence,
      status: status ?? this.status,
      sentenceIds: List.unmodifiable(sentenceIds ?? this.sentenceIds),
    );
  }
}

class StudyGroupSentence {
  const StudyGroupSentence({
    required this.groupId,
    required this.sentenceId,
    required this.position,
  });

  final String groupId;
  final String sentenceId;
  final int position;
}

abstract class StudyDatabase {
  Future<StudySettings> readStudySettings();

  Future<StudySession?> findStudySessionByDate(DateTime studyDate);

  Future<StudySession> createStudySession(DateTime studyDate);

  Future<void> updateStudySession(StudySession session);

  Future<List<StudyGroup>> findStudyGroupsForSession(String sessionId);

  Future<StudyGroup> createStudyGroup({
    required String sessionId,
    required StudyGroupType type,
    required int sequence,
    required List<String> sentenceIds,
  });

  Future<void> updateStudyGroup(StudyGroup group);

  Future<List<StudySentence>> findReviewSentencesDueBefore(
    DateTime todayEnd,
  );

  Future<List<StudySentence>> findUnlearnedSentences({
    required int limit,
  });
}

class InMemoryStudyDatabase implements StudyDatabase {
  InMemoryStudyDatabase({
    required StudySettings settings,
    Iterable<StudySentence> sentences = const <StudySentence>[],
  })  : _settings = settings,
        _sentences = {for (final sentence in sentences) sentence.id: sentence};

  StudySettings _settings;
  final Map<String, StudySentence> _sentences;
  final Map<String, StudySession> _sessions = <String, StudySession>{};
  final Map<String, StudyGroup> _groups = <String, StudyGroup>{};
  final List<StudyGroupSentence> _groupSentences = <StudyGroupSentence>[];
  int _sessionSequence = 0;
  int _groupSequence = 0;

  List<StudyGroupSentence> get groupSentences =>
      List.unmodifiable(_groupSentences);

  set settings(StudySettings value) => _settings = value;

  @override
  Future<StudySettings> readStudySettings() async => _settings;

  @override
  Future<StudySession?> findStudySessionByDate(DateTime studyDate) async {
    final normalized = normalizeStudyDate(studyDate);
    for (final session in _sessions.values) {
      if (session.studyDate == normalized) return session;
    }
    return null;
  }

  @override
  Future<StudySession> createStudySession(DateTime studyDate) async {
    final normalized = normalizeStudyDate(studyDate);
    final existing = await findStudySessionByDate(normalized);
    if (existing != null) return existing;

    final session = StudySession(
      id: 'session_${++_sessionSequence}',
      studyDate: normalized,
    );
    _sessions[session.id] = session;
    return session;
  }

  @override
  Future<void> updateStudySession(StudySession session) async {
    _sessions[session.id] = session;
  }

  @override
  Future<List<StudyGroup>> findStudyGroupsForSession(String sessionId) async {
    final groups = _groups.values
        .where((group) => group.sessionId == sessionId)
        .toList()
      ..sort((a, b) => a.sequence.compareTo(b.sequence));
    return List.unmodifiable(groups);
  }

  @override
  Future<StudyGroup> createStudyGroup({
    required String sessionId,
    required StudyGroupType type,
    required int sequence,
    required List<String> sentenceIds,
  }) async {
    final group = StudyGroup(
      id: 'group_${++_groupSequence}',
      sessionId: sessionId,
      type: type,
      sequence: sequence,
      sentenceIds: List.unmodifiable(sentenceIds),
    );
    _groups[group.id] = group;
    for (var index = 0; index < sentenceIds.length; index += 1) {
      _groupSentences.add(
        StudyGroupSentence(
          groupId: group.id,
          sentenceId: sentenceIds[index],
          position: index,
        ),
      );
    }
    return group;
  }

  @override
  Future<void> updateStudyGroup(StudyGroup group) async {
    _groups[group.id] = group;
  }

  @override
  Future<List<StudySentence>> findReviewSentencesDueBefore(
    DateTime todayEnd,
  ) async {
    return _sentences.values.where((sentence) {
      final nextReviewAt = sentence.nextReviewAt;
      return sentence.forceReviewTomorrow ||
          (nextReviewAt != null && !nextReviewAt.isAfter(todayEnd));
    }).toList();
  }

  @override
  Future<List<StudySentence>> findUnlearnedSentences({
    required int limit,
  }) async {
    if (limit <= 0) return const <StudySentence>[];
    final result = _sentences.values
        .where((sentence) => !sentence.hasBeenStudied)
        .take(limit)
        .toList();
    return List.unmodifiable(result);
  }
}
