import 'package:flutter/material.dart';

/// The learning phase used when persisting study activity.
enum StudyPhase { study, dictation }

/// The stage a study group is currently in.
enum StudyGroupStage { study, dictation, completed }

/// A group of sentences that should be studied together.
@immutable
class StudyGroup {
  const StudyGroup({
    required this.id,
    required this.title,
    required this.completedStudyCount,
    required this.stage,
  });

  final String id;
  final String title;
  final int completedStudyCount;
  final StudyGroupStage stage;

  StudyGroup copyWith({
    String? id,
    String? title,
    int? completedStudyCount,
    StudyGroupStage? stage,
  }) {
    return StudyGroup(
      id: id ?? this.id,
      title: title ?? this.title,
      completedStudyCount: completedStudyCount ?? this.completedStudyCount,
      stage: stage ?? this.stage,
    );
  }
}

/// A vocabulary item extracted from a Japanese sentence.
@immutable
class VocabularyBreakdown {
  const VocabularyBreakdown({
    required this.surface,
    required this.reading,
    required this.meaning,
    this.partOfSpeech,
  });

  final String surface;
  final String reading;
  final String meaning;
  final String? partOfSpeech;
}

/// A sentence that belongs to a [StudyGroup].
@immutable
class StudySentence {
  const StudySentence({
    required this.id,
    required this.groupId,
    required this.japaneseText,
    required this.kanaText,
    required this.chineseTranslation,
    required this.jlptLevel,
    required this.vocabularyBreakdown,
    required this.grammarExplanation,
    this.romajiText,
    this.audioUrl,
  });

  final String id;
  final String groupId;
  final String japaneseText;
  final String kanaText;
  final String? romajiText;
  final String chineseTranslation;
  final String jlptLevel;
  final List<VocabularyBreakdown> vocabularyBreakdown;
  final String grammarExplanation;
  final String? audioUrl;
}

/// Persisted activity for a sentence shown during the study phase.
@immutable
class StudyRecord {
  const StudyRecord({
    required this.groupId,
    required this.sentenceId,
    required this.studyPhase,
    required this.studiedAt,
  });

  final String groupId;
  final String sentenceId;
  final StudyPhase studyPhase;
  final DateTime studiedAt;
}

/// Loads sentences for a study group.
abstract class StudySentenceRepository {
  Future<List<StudySentence>> loadSentencesByGroupId(String groupId);
}

/// Persists study records.
abstract class StudyRecordRepository {
  Future<void> upsertStudyRecord(StudyRecord record);
}

/// Persists study-group progress and stage transitions.
abstract class StudyGroupRepository {
  Future<void> updateCompletedStudyCount({
    required String groupId,
    required int completedStudyCount,
  });

  Future<void> advanceToDictationStage(String groupId);
}

/// Plays sentence audio when available.
typedef StudyAudioPlayer = Future<void> Function(StudySentence sentence);

/// Screen for studying every sentence in a [StudyGroup] before dictation.
class StudyPage extends StatefulWidget {
  const StudyPage({
    super.key,
    required this.group,
    required this.sentenceRepository,
    required this.recordRepository,
    required this.groupRepository,
    this.audioPlayer,
    this.onGroupAdvancedToDictation,
  });

  final StudyGroup group;
  final StudySentenceRepository sentenceRepository;
  final StudyRecordRepository recordRepository;
  final StudyGroupRepository groupRepository;
  final StudyAudioPlayer? audioPlayer;
  final ValueChanged<StudyGroup>? onGroupAdvancedToDictation;

  @override
  State<StudyPage> createState() => _StudyPageState();
}

class _StudyPageState extends State<StudyPage> {
  late Future<List<StudySentence>> _sentencesFuture;
  final Set<String> _recordedSentenceIds = <String>{};

  var _currentIndex = 0;
  var _showRomaji = false;
  var _isSavingProgress = false;
  var _isPlayingAudio = false;
  StudyGroup? _advancedGroup;

  @override
  void initState() {
    super.initState();
    _sentencesFuture = widget.sentenceRepository
        .loadSentencesByGroupId(widget.group.id)
        .then(_restoreSavedProgress);
  }

  @override
  void didUpdateWidget(covariant StudyPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.group.id != widget.group.id ||
        oldWidget.sentenceRepository != widget.sentenceRepository) {
      _resetAndLoadSentences();
    }
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<List<StudySentence>>(
      future: _sentencesFuture,
      builder: (context, snapshot) {
        return Scaffold(
          appBar: AppBar(
            title: Text(widget.group.title),
          ),
          body: _buildBody(context, snapshot),
        );
      },
    );
  }

  Widget _buildBody(
    BuildContext context,
    AsyncSnapshot<List<StudySentence>> snapshot,
  ) {
    if (snapshot.connectionState != ConnectionState.done) {
      return const Center(child: CircularProgressIndicator());
    }

    if (snapshot.hasError) {
      return _MessageState(
        icon: Icons.error_outline,
        title: '无法加载学习内容',
        message: '${snapshot.error}',
        actionLabel: '重试',
        onActionPressed: _reloadSentences,
      );
    }

    final sentences = snapshot.data ?? const <StudySentence>[];
    if (sentences.isEmpty) {
      return const _MessageState(
        icon: Icons.inbox_outlined,
        title: '当前学习组没有句子',
        message: '请先为该组导入句子后再开始学习。',
      );
    }

    if (_advancedGroup != null) {
      return _CompletionView(
        group: _advancedGroup!,
        totalSentenceCount: sentences.length,
      );
    }

    final safeIndex = _currentIndex.clamp(0, sentences.length - 1).toInt();
    final sentence = sentences[safeIndex];
    final completedCount = _completedCountFor(sentences.length);

    return SafeArea(
      child: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          _ProgressHeader(
            completedCount: completedCount,
            totalCount: sentences.length,
            currentIndex: safeIndex,
          ),
          const SizedBox(height: 20),
          _SentenceCard(
            sentence: sentence,
            showRomaji: _showRomaji,
            onShowRomajiChanged: (value) {
              setState(() => _showRomaji = value);
            },
          ),
          const SizedBox(height: 16),
          _VocabularySection(items: sentence.vocabularyBreakdown),
          const SizedBox(height: 16),
          _GrammarSection(explanation: sentence.grammarExplanation),
          const SizedBox(height: 24),
          Row(
            children: [
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: sentence.audioUrl == null ||
                          widget.audioPlayer == null ||
                          _isPlayingAudio
                      ? null
                      : () => _playAudio(sentence),
                  icon: _isPlayingAudio
                      ? const SizedBox.square(
                          dimension: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.volume_up_outlined),
                  label: const Text('播放音频'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: FilledButton.icon(
                  onPressed: _isSavingProgress
                      ? null
                      : () => _recordAndMoveNext(sentences, sentence),
                  icon: _isSavingProgress
                      ? const SizedBox.square(
                          dimension: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.navigate_next),
                  label: Text(
                    safeIndex == sentences.length - 1 ? '完成学习' : '下一句',
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  List<StudySentence> _restoreSavedProgress(List<StudySentence> sentences) {
    if (sentences.isEmpty) {
      return sentences;
    }

    final restoredIndex = widget.group.completedStudyCount
        .clamp(0, sentences.length - 1)
        .toInt();
    if (mounted) {
      setState(() => _currentIndex = restoredIndex);
    } else {
      _currentIndex = restoredIndex;
    }
    return sentences;
  }

  int _completedCountFor(int totalCount) {
    final baseline = widget.group.completedStudyCount
        .clamp(0, totalCount)
        .toInt();
    final currentSessionCount = _recordedSentenceIds.length
        .clamp(0, totalCount)
        .toInt();
    final currentSequentialCount = _currentIndex
        .clamp(0, totalCount)
        .toInt();

    return <int>[baseline, currentSessionCount, currentSequentialCount]
        .reduce((value, element) => value > element ? value : element);
  }

  int _nextCompletedCount(int totalCount) {
    final nextSequentialCount = (_currentIndex + 1)
        .clamp(0, totalCount)
        .toInt();
    final currentSessionCount = _recordedSentenceIds.length
        .clamp(0, totalCount)
        .toInt();
    final baseline = widget.group.completedStudyCount
        .clamp(0, totalCount)
        .toInt();

    return <int>[baseline, currentSessionCount, nextSequentialCount]
        .reduce((value, element) => value > element ? value : element);
  }

  Future<void> _recordAndMoveNext(
    List<StudySentence> sentences,
    StudySentence sentence,
  ) async {
    setState(() => _isSavingProgress = true);

    try {
      if (!_recordedSentenceIds.contains(sentence.id)) {
        await widget.recordRepository.upsertStudyRecord(
          StudyRecord(
            groupId: widget.group.id,
            sentenceId: sentence.id,
            studyPhase: StudyPhase.study,
            studiedAt: DateTime.now(),
          ),
        );
        _recordedSentenceIds.add(sentence.id);
      }

      final completedStudyCount = _nextCompletedCount(sentences.length);
      await widget.groupRepository.updateCompletedStudyCount(
        groupId: widget.group.id,
        completedStudyCount: completedStudyCount,
      );

      if (_currentIndex >= sentences.length - 1) {
        await widget.groupRepository.advanceToDictationStage(widget.group.id);
        final advancedGroup = widget.group.copyWith(
          completedStudyCount: sentences.length,
          stage: StudyGroupStage.dictation,
        );
        widget.onGroupAdvancedToDictation?.call(advancedGroup);
        if (!mounted) {
          return;
        }
        setState(() {
          _advancedGroup = advancedGroup;
          _isSavingProgress = false;
        });
        return;
      }

      if (!mounted) {
        return;
      }
      setState(() {
        _currentIndex += 1;
        _isSavingProgress = false;
      });
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() => _isSavingProgress = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('学习进度保存失败：$error')),
      );
    }
  }

  Future<void> _playAudio(StudySentence sentence) async {
    final audioPlayer = widget.audioPlayer;
    if (audioPlayer == null) {
      return;
    }

    setState(() => _isPlayingAudio = true);
    try {
      await audioPlayer(sentence);
    } catch (error) {
      if (!mounted) {
        return;
      }
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('音频播放失败：$error')),
      );
    } finally {
      if (mounted) {
        setState(() => _isPlayingAudio = false);
      }
    }
  }

  void _reloadSentences() {
    setState(_resetAndLoadSentences);
  }

  void _resetAndLoadSentences() {
    _currentIndex = 0;
    _advancedGroup = null;
    _recordedSentenceIds.clear();
    _sentencesFuture = widget.sentenceRepository
        .loadSentencesByGroupId(widget.group.id)
        .then(_restoreSavedProgress);
  }
}

class _ProgressHeader extends StatelessWidget {
  const _ProgressHeader({
    required this.completedCount,
    required this.totalCount,
    required this.currentIndex,
  });

  final int completedCount;
  final int totalCount;
  final int currentIndex;

  @override
  Widget build(BuildContext context) {
    final progress = totalCount == 0 ? 0.0 : completedCount / totalCount;
    final theme = Theme.of(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              '当前组进度',
              style: theme.textTheme.titleMedium,
            ),
            Text('$completedCount / $totalCount'),
          ],
        ),
        const SizedBox(height: 8),
        LinearProgressIndicator(value: progress),
        const SizedBox(height: 8),
        Text('正在学习第 ${currentIndex + 1} 句'),
      ],
    );
  }
}

class _SentenceCard extends StatelessWidget {
  const _SentenceCard({
    required this.sentence,
    required this.showRomaji,
    required this.onShowRomajiChanged,
  });

  final StudySentence sentence;
  final bool showRomaji;
  final ValueChanged<bool> onShowRomajiChanged;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final hasRomaji = sentence.romajiText?.trim().isNotEmpty ?? false;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Chip(label: Text(sentence.jlptLevel)),
                const Spacer(),
                Text('罗马音'),
                Switch(
                  value: showRomaji,
                  onChanged: hasRomaji ? onShowRomajiChanged : null,
                ),
              ],
            ),
            const SizedBox(height: 12),
            Text(
              sentence.japaneseText,
              style: theme.textTheme.headlineSmall,
            ),
            const SizedBox(height: 8),
            Text(
              sentence.kanaText,
              style: theme.textTheme.titleMedium?.copyWith(
                color: theme.colorScheme.secondary,
              ),
            ),
            if (showRomaji && hasRomaji) ...[
              const SizedBox(height: 8),
              Text(
                sentence.romajiText!,
                style: theme.textTheme.bodyLarge?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
            ],
            const Divider(height: 32),
            Text(
              sentence.chineseTranslation,
              style: theme.textTheme.titleMedium,
            ),
          ],
        ),
      ),
    );
  }
}

class _VocabularySection extends StatelessWidget {
  const _VocabularySection({required this.items});

  final List<VocabularyBreakdown> items;

  @override
  Widget build(BuildContext context) {
    return _StudySection(
      title: '词汇拆解',
      child: items.isEmpty
          ? const Text('暂无词汇拆解。')
          : Column(
              children: items.map((item) {
                return ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text('${item.surface}（${item.reading}）'),
                  subtitle: Text(
                    item.partOfSpeech == null
                        ? item.meaning
                        : '${item.partOfSpeech} · ${item.meaning}',
                  ),
                );
              }).toList(),
            ),
    );
  }
}

class _GrammarSection extends StatelessWidget {
  const _GrammarSection({required this.explanation});

  final String explanation;

  @override
  Widget build(BuildContext context) {
    return _StudySection(
      title: '语法说明',
      child: Text(
        explanation.trim().isEmpty ? '暂无语法说明。' : explanation,
      ),
    );
  }
}

class _StudySection extends StatelessWidget {
  const _StudySection({
    required this.title,
    required this.child,
  });

  final String title;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 12),
            child,
          ],
        ),
      ),
    );
  }
}

class _CompletionView extends StatelessWidget {
  const _CompletionView({
    required this.group,
    required this.totalSentenceCount,
  });

  final StudyGroup group;
  final int totalSentenceCount;

  @override
  Widget build(BuildContext context) {
    return _MessageState(
      icon: Icons.check_circle_outline,
      title: '学习阶段已完成',
      message: '已学习 $totalSentenceCount 句，${group.title} 已推进到默写阶段。',
    );
  }
}

class _MessageState extends StatelessWidget {
  const _MessageState({
    required this.icon,
    required this.title,
    required this.message,
    this.actionLabel,
    this.onActionPressed,
  });

  final IconData icon;
  final String title;
  final String message;
  final String? actionLabel;
  final VoidCallback? onActionPressed;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 56, color: theme.colorScheme.primary),
            const SizedBox(height: 16),
            Text(title, style: theme.textTheme.titleLarge),
            const SizedBox(height: 8),
            Text(
              message,
              textAlign: TextAlign.center,
              style: theme.textTheme.bodyMedium,
            ),
            if (actionLabel != null && onActionPressed != null) ...[
              const SizedBox(height: 16),
              FilledButton(
                onPressed: onActionPressed,
                child: Text(actionLabel!),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
