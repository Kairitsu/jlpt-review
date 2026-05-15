import 'package:flutter/material.dart';

import '../models/dictation_prompt.dart';
import '../models/dictation_result.dart';
import '../models/dictation_settings.dart';
import '../models/study_record.dart';
import '../services/dictation_judge.dart';

typedef StudyRecordWriter = Future<void> Function(StudyRecord record);
typedef DictationAudioPlayer = Future<void> Function(DictationPrompt prompt);

/// MVP dictation page: translation cue, free-form input, submit/skip, hint/audio,
/// and queue progress. It deliberately does not provide multiple choice,
/// word-bank sentence building, or drag-and-drop answers.
class DictationPage extends StatefulWidget {
  const DictationPage({
    super.key,
    required this.queue,
    required this.onRecord,
    this.settings = const DictationSettings(),
    this.judge = const DictationJudge(),
    this.onPlayAudio,
    this.onComplete,
  });

  final List<DictationPrompt> queue;
  final StudyRecordWriter onRecord;
  final DictationSettings settings;
  final DictationJudge judge;
  final DictationAudioPlayer? onPlayAudio;
  final VoidCallback? onComplete;

  @override
  State<DictationPage> createState() => _DictationPageState();
}

class _DictationPageState extends State<DictationPage> {
  final TextEditingController _answerController = TextEditingController();
  int _currentIndex = 0;
  bool _showHint = false;
  DictationResult? _lastResult;

  DictationPrompt? get _currentPrompt {
    if (widget.queue.isEmpty || _currentIndex >= widget.queue.length) {
      return null;
    }
    return widget.queue[_currentIndex];
  }

  @override
  void dispose() {
    _answerController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final prompt = _currentPrompt;
    if (prompt == null) return;

    final result = widget.judge.judge(
      prompt: prompt,
      input: _answerController.text,
      settings: widget.settings,
    );

    await widget.onRecord(
      StudyRecord(
        itemId: prompt.id,
        studyPhase: 'dictation',
        answer: _answerController.text,
        result: result.status,
        createdAt: DateTime.now(),
      ),
    );

    if (!mounted) return;
    setState(() => _lastResult = result);
    _advance();
  }

  Future<void> _skip() async {
    final prompt = _currentPrompt;
    if (prompt == null) return;

    await widget.onRecord(
      StudyRecord(
        itemId: prompt.id,
        studyPhase: 'dictation',
        answer: _answerController.text,
        result: DictationResultStatus.wrong,
        createdAt: DateTime.now(),
        skipped: true,
      ),
    );

    if (!mounted) return;
    _advance();
  }

  void _advance() {
    setState(() {
      _answerController.clear();
      _showHint = false;
      _currentIndex += 1;
    });

    if (_currentIndex >= widget.queue.length) {
      widget.onComplete?.call();
    }
  }

  @override
  Widget build(BuildContext context) {
    final prompt = _currentPrompt;
    final total = widget.queue.length;
    final completed = _currentIndex.clamp(0, total);

    if (prompt == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('默写')),
        body: Center(child: Text('已完成 $completed / $total')),
      );
    }

    return Scaffold(
      appBar: AppBar(title: const Text('默写')),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                '进度：${_currentIndex + 1} / $total',
                style: Theme.of(context).textTheme.labelLarge,
              ),
              const SizedBox(height: 24),
              Text(
                '中文翻译',
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 8),
              Text(
                prompt.translationZh,
                style: Theme.of(context).textTheme.headlineSmall,
              ),
              const SizedBox(height: 24),
              TextField(
                controller: _answerController,
                minLines: 1,
                maxLines: 4,
                textInputAction: TextInputAction.done,
                decoration: const InputDecoration(
                  border: OutlineInputBorder(),
                  labelText: '请输入日文答案',
                ),
                onSubmitted: (_) => _submit(),
              ),
              const SizedBox(height: 16),
              Row(
                children: [
                  Expanded(
                    child: FilledButton(
                      onPressed: _submit,
                      child: const Text('提交'),
                    ),
                  ),
                  const SizedBox(width: 12),
                  OutlinedButton(
                    onPressed: _skip,
                    child: const Text('跳过'),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 12,
                children: [
                  if (prompt.hint != null)
                    TextButton.icon(
                      onPressed: () => setState(() => _showHint = !_showHint),
                      icon: const Icon(Icons.lightbulb_outline),
                      label: const Text('提示'),
                    ),
                  if (prompt.audioUrl != null && widget.onPlayAudio != null)
                    TextButton.icon(
                      onPressed: () => widget.onPlayAudio!(prompt),
                      icon: const Icon(Icons.volume_up_outlined),
                      label: const Text('播放音频'),
                    ),
                ],
              ),
              if (_showHint && prompt.hint != null) ...[
                const SizedBox(height: 8),
                Text('提示：${prompt.hint}'),
              ],
              if (_lastResult != null) ...[
                const SizedBox(height: 16),
                Text('上次结果：${_lastResult!.status.name}'),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
