import 'dart:collection';

/// Dictation progress status for a study group.
enum StudyGroupStatus {
  notStarted,
  dictationInProgress,
  dictationCompleted,
}

/// Minimal study-group state that the dictation queue controller updates.
class StudyGroup {
  StudyGroup({
    required this.id,
    this.completedDictationCount = 0,
    this.status = StudyGroupStatus.notStarted,
  });

  final String id;
  int completedDictationCount;
  StudyGroupStatus status;
}

/// A sentence that belongs to a study group's dictation phase.
class DictationSentence {
  const DictationSentence({
    required this.id,
    required this.answer,
    this.prompt,
  });

  final String id;
  final String answer;
  final String? prompt;
}

/// A persisted successful dictation record.
class DictationCorrectRecord {
  const DictationCorrectRecord({
    required this.groupId,
    required this.sentenceId,
    required this.userAnswer,
    required this.correctAnswer,
    required this.submittedAt,
  });

  final String groupId;
  final String sentenceId;
  final String userAnswer;
  final String correctAnswer;
  final DateTime submittedAt;
}

/// Review-state update emitted after a sentence is answered correctly.
class DictationReviewUpdate {
  const DictationReviewUpdate({
    required this.groupId,
    required this.sentenceId,
    required this.reviewedAt,
  });

  final String groupId;
  final String sentenceId;
  final DateTime reviewedAt;
}

/// Result returned to the page after every dictation submission.
class DictationSubmissionResult {
  const DictationSubmissionResult({
    required this.sentence,
    required this.userAnswer,
    required this.correctAnswer,
    required this.isCorrect,
    required this.errorExplanation,
    required this.completedGroup,
    required this.remainingQueueLength,
  });

  final DictationSentence sentence;
  final String userAnswer;
  final String correctAnswer;
  final bool isCorrect;

  /// Short explanation to display when [isCorrect] is false.
  final String? errorExplanation;

  /// True only after the queue is empty.
  final bool completedGroup;
  final int remainingQueueLength;
}

/// Controls the in-group dictation queue.
///
/// The controller removes the current sentence from the head of the queue on
/// every submission. Correct answers are recorded and never re-added. Wrong
/// answers return display information for the page and are appended to the tail,
/// so the learner sees other queued sentences before retrying the missed one.
class DictationQueueController {
  DictationQueueController({
    bool Function(String userAnswer, String correctAnswer)? answerMatcher,
    String Function(String userAnswer, String correctAnswer)? explainError,
    DateTime Function()? clock,
  })  : _answerMatcher = answerMatcher ?? _defaultAnswerMatcher,
        _explainError = explainError ?? _defaultExplainError,
        _clock = clock ?? DateTime.now;

  final bool Function(String userAnswer, String correctAnswer) _answerMatcher;
  final String Function(String userAnswer, String correctAnswer) _explainError;
  final DateTime Function() _clock;

  StudyGroup? _group;
  final Queue<DictationSentence> _queue = Queue<DictationSentence>();
  final List<DictationCorrectRecord> _correctRecords =
      <DictationCorrectRecord>[];
  final List<DictationReviewUpdate> _reviewUpdates = <DictationReviewUpdate>[];

  /// Initializes the dictation phase with all sentences from the current group.
  void startGroup({
    required StudyGroup group,
    required Iterable<DictationSentence> sentences,
  }) {
    _group = group;
    _queue
      ..clear()
      ..addAll(sentences);
    _correctRecords.clear();
    _reviewUpdates.clear();
    group.completedDictationCount = 0;
    group.status = _queue.isEmpty
        ? StudyGroupStatus.dictationCompleted
        : StudyGroupStatus.dictationInProgress;
  }

  /// Current sentence at the queue head, or null after completion.
  DictationSentence? get currentSentence =>
      _queue.isEmpty ? null : _queue.first;

  /// Immutable snapshot of queued sentence ids, useful for UI state and tests.
  List<String> get queuedSentenceIds =>
      List<String>.unmodifiable(_queue.map((sentence) => sentence.id));

  /// Successful answer records written during the current dictation run.
  List<DictationCorrectRecord> get correctRecords =>
      List<DictationCorrectRecord>.unmodifiable(_correctRecords);

  /// Review-state updates emitted during the current dictation run.
  List<DictationReviewUpdate> get reviewUpdates =>
      List<DictationReviewUpdate>.unmodifiable(_reviewUpdates);

  bool get isCompleted => _queue.isEmpty;

  /// Submits an answer for the current sentence and advances the queue.
  DictationSubmissionResult submit(String userAnswer) {
    final group = _requireStartedGroup();
    if (_queue.isEmpty) {
      throw StateError('Cannot submit dictation after the queue is complete.');
    }

    final sentence = _queue.removeFirst();
    final correct = _answerMatcher(userAnswer, sentence.answer);
    final submittedAt = _clock();
    String? errorExplanation;

    if (correct) {
      _correctRecords.add(
        DictationCorrectRecord(
          groupId: group.id,
          sentenceId: sentence.id,
          userAnswer: userAnswer,
          correctAnswer: sentence.answer,
          submittedAt: submittedAt,
        ),
      );
      _reviewUpdates.add(
        DictationReviewUpdate(
          groupId: group.id,
          sentenceId: sentence.id,
          reviewedAt: submittedAt,
        ),
      );
      group.completedDictationCount += 1;
    } else {
      errorExplanation = _explainError(userAnswer, sentence.answer);
      _queue.addLast(sentence);
    }

    group.status = _queue.isEmpty
        ? StudyGroupStatus.dictationCompleted
        : StudyGroupStatus.dictationInProgress;

    return DictationSubmissionResult(
      sentence: sentence,
      userAnswer: userAnswer,
      correctAnswer: sentence.answer,
      isCorrect: correct,
      errorExplanation: errorExplanation,
      completedGroup: _queue.isEmpty,
      remainingQueueLength: _queue.length,
    );
  }

  StudyGroup _requireStartedGroup() {
    final group = _group;
    if (group == null) {
      throw StateError('Call startGroup before submitting dictation answers.');
    }
    return group;
  }

  static bool _defaultAnswerMatcher(String userAnswer, String correctAnswer) =>
      _normalize(userAnswer) == _normalize(correctAnswer);

  static String _defaultExplainError(String userAnswer, String correctAnswer) {
    if (userAnswer.trim().isEmpty) {
      return '答案为空，请对照正确答案后稍后重试。';
    }
    return '答案与正确句子不一致，请比较遗漏、顺序或用字差异后重试。';
  }

  static String _normalize(String value) =>
      value.trim().replaceAll(RegExp(r'\s+'), ' ');
}
