import 'package:flutter/material.dart';

/// Status values used when creating and updating rows in the `sentence` table.
enum SentenceJobStatus {
  pending,
  processing,
  completed,
  failed;

  String get databaseValue => name;
}

/// A pending `sentence` row that still needs LLM parsing.
class PendingSentence {
  const PendingSentence({
    required this.id,
    required this.sourceSentence,
  });

  final int id;
  final String sourceSentence;
}

/// Parsed sentence data returned by the LLM parser.
///
/// Keep [payload] adapter-friendly so infrastructure code can persist the
/// parser's JSON output without coupling this feature page to a specific schema.
class ParsedSentence {
  const ParsedSentence({required this.payload});

  final Map<String, Object?> payload;
}

/// Persistence boundary for the import flow.
///
/// Implementations should query `Sentences.sourceSentence` for de-duplication
/// and write new rows into the `sentence` table with both parse and TTS statuses
/// set to `pending`.
abstract class ImportSentenceRepository {
  /// Returns source sentences that already exist in `Sentences.sourceSentence`.
  Future<Set<String>> findExistingSourceSentences(
    Iterable<String> sourceSentences,
  );

  /// Inserts each source sentence into `sentence` with pending initial statuses.
  Future<void> insertPendingSentences(Iterable<String> sourceSentences);

  /// Returns sentences whose parse status is `pending`.
  Future<List<PendingSentence>> fetchPendingParseSentences();

  /// Returns sentences whose parse status is `failed`.
  Future<List<PendingSentence>> fetchFailedParseSentences();

  /// Marks a sentence as actively being parsed.
  Future<void> markParseProcessing(int sentenceId);

  /// Persists parser output and marks parse status completed.
  Future<void> markParseSucceeded(int sentenceId, ParsedSentence parsedSentence);

  /// Stores parser failure details and marks parse status failed.
  Future<void> markParseFailed(int sentenceId, Object error, StackTrace stackTrace);
}

/// LLM boundary for parsing imported Japanese sentences.
abstract class LlmSentenceParser {
  Future<ParsedSentence> parse(String sourceSentence);
}

class ImportPreview {
  const ImportPreview({
    required this.totalCount,
    required this.newSentences,
    required this.duplicateSentences,
  });

  final int totalCount;
  final List<String> newSentences;
  final List<String> duplicateSentences;

  int get newCount => newSentences.length;
  int get duplicateCount => duplicateSentences.length;
  bool get hasNewSentences => newSentences.isNotEmpty;
}

class ImportPage extends StatefulWidget {
  const ImportPage({
    super.key,
    required this.repository,
    required this.parser,
  });

  final ImportSentenceRepository repository;
  final LlmSentenceParser parser;

  @override
  State<ImportPage> createState() => _ImportPageState();
}

class _ImportPageState extends State<ImportPage> {
  final TextEditingController _textController = TextEditingController();

  ImportPreview? _preview;
  List<PendingSentence> _failedSentences = const [];
  bool _isPreviewing = false;
  bool _isImporting = false;
  bool _isRetrying = false;
  String? _errorMessage;

  @override
  void initState() {
    super.initState();
    _loadFailedSentences();
  }

  @override
  void dispose() {
    _textController.dispose();
    super.dispose();
  }

  Future<void> _buildPreview() async {
    final cleanedSentences = cleanImportedSentences(_textController.text);
    if (cleanedSentences.isEmpty) {
      setState(() {
        _preview = const ImportPreview(
          totalCount: 0,
          newSentences: [],
          duplicateSentences: [],
        );
        _errorMessage = null;
      });
      return;
    }

    setState(() {
      _isPreviewing = true;
      _errorMessage = null;
    });

    try {
      final existingSentences =
          await widget.repository.findExistingSourceSentences(cleanedSentences);
      final duplicateSentences = <String>[];
      final newSentences = <String>[];
      final seenNewSentences = <String>{};

      for (final sentence in cleanedSentences) {
        final alreadyExists = existingSentences.contains(sentence);
        final duplicatedInBatch = seenNewSentences.contains(sentence);
        if (alreadyExists || duplicatedInBatch) {
          duplicateSentences.add(sentence);
        } else {
          seenNewSentences.add(sentence);
          newSentences.add(sentence);
        }
      }

      if (!mounted) return;
      setState(() {
        _preview = ImportPreview(
          totalCount: cleanedSentences.length,
          newSentences: List.unmodifiable(newSentences),
          duplicateSentences: List.unmodifiable(duplicateSentences),
        );
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _errorMessage = '无法生成导入预览：$error';
      });
    } finally {
      if (mounted) {
        setState(() {
          _isPreviewing = false;
        });
      }
    }
  }

  Future<void> _confirmImport() async {
    final preview = _preview;
    if (preview == null || !preview.hasNewSentences) return;

    setState(() {
      _isImporting = true;
      _errorMessage = null;
    });

    try {
      await widget.repository.insertPendingSentences(preview.newSentences);
      await _parsePendingSentences(includeFailed: false);
      _textController.clear();

      if (!mounted) return;
      setState(() {
        _preview = null;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _errorMessage = '导入失败：$error';
      });
    } finally {
      await _loadFailedSentences();
      if (mounted) {
        setState(() {
          _isImporting = false;
        });
      }
    }
  }

  Future<void> _retryFailedParsing() async {
    setState(() {
      _isRetrying = true;
      _errorMessage = null;
    });

    try {
      await _parsePendingSentences(includeFailed: true);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _errorMessage = '重试失败：$error';
      });
    } finally {
      await _loadFailedSentences();
      if (mounted) {
        setState(() {
          _isRetrying = false;
        });
      }
    }
  }

  Future<void> _parsePendingSentences({required bool includeFailed}) async {
    final pendingSentences = includeFailed
        ? await widget.repository.fetchFailedParseSentences()
        : await widget.repository.fetchPendingParseSentences();

    for (final sentence in pendingSentences) {
      try {
        await widget.repository.markParseProcessing(sentence.id);
        final parsedSentence =
            await widget.parser.parse(sentence.sourceSentence);
        await widget.repository.markParseSucceeded(
          sentence.id,
          parsedSentence,
        );
      } catch (error, stackTrace) {
        await widget.repository.markParseFailed(
          sentence.id,
          error,
          stackTrace,
        );
      }
    }
  }

  Future<void> _loadFailedSentences() async {
    try {
      final failedSentences =
          await widget.repository.fetchFailedParseSentences();
      if (!mounted) return;
      setState(() {
        _failedSentences = List.unmodifiable(failedSentences);
      });
    } catch (_) {
      // Failure list loading should not block the import form.
    }
  }

  @override
  Widget build(BuildContext context) {
    final preview = _preview;
    final isBusy = _isPreviewing || _isImporting || _isRetrying;

    return Scaffold(
      appBar: AppBar(
        title: const Text('导入句子'),
      ),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            TextField(
              controller: _textController,
              enabled: !isBusy,
              minLines: 8,
              maxLines: 16,
              decoration: const InputDecoration(
                border: OutlineInputBorder(),
                labelText: '待导入文本',
                alignLabelWithHint: true,
                hintText: '每行输入一个候选句子',
              ),
              textInputAction: TextInputAction.newline,
              keyboardType: TextInputType.multiline,
            ),
            const SizedBox(height: 12),
            FilledButton.icon(
              onPressed: isBusy ? null : _buildPreview,
              icon: _isPreviewing
                  ? const SizedBox.square(
                      dimension: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.preview),
              label: const Text('生成导入预览'),
            ),
            if (_errorMessage != null) ...[
              const SizedBox(height: 12),
              Text(
                _errorMessage!,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ],
            if (preview != null) ...[
              const SizedBox(height: 16),
              _ImportPreviewCard(
                preview: preview,
                isImporting: _isImporting,
                onConfirm:
                    preview.hasNewSentences && !isBusy ? _confirmImport : null,
              ),
            ],
            const SizedBox(height: 16),
            _FailedParseCard(
              failedSentences: _failedSentences,
              isRetrying: _isRetrying,
              onRetry:
                  _failedSentences.isEmpty || isBusy
                      ? null
                      : _retryFailedParsing,
            ),
          ],
        ),
      ),
    );
  }
}

class _ImportPreviewCard extends StatelessWidget {
  const _ImportPreviewCard({
    required this.preview,
    required this.isImporting,
    required this.onConfirm,
  });

  final ImportPreview preview;
  final bool isImporting;
  final VoidCallback? onConfirm;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '导入预览',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 12),
            _PreviewCountRow(label: '总数', count: preview.totalCount),
            _PreviewCountRow(label: '新句子数', count: preview.newCount),
            _PreviewCountRow(label: '重复句子数', count: preview.duplicateCount),
            if (preview.duplicateSentences.isNotEmpty) ...[
              const SizedBox(height: 12),
              Text(
                '重复句子会被跳过。',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
            const SizedBox(height: 12),
            Align(
              alignment: Alignment.centerRight,
              child: FilledButton.icon(
                onPressed: onConfirm,
                icon: isImporting
                    ? const SizedBox.square(
                        dimension: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.file_upload),
                label: const Text('确认导入并解析'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _PreviewCountRow extends StatelessWidget {
  const _PreviewCountRow({required this.label, required this.count});

  final String label;
  final int count;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label),
          Text(
            count.toString(),
            style: Theme.of(context).textTheme.titleSmall,
          ),
        ],
      ),
    );
  }
}

class _FailedParseCard extends StatelessWidget {
  const _FailedParseCard({
    required this.failedSentences,
    required this.isRetrying,
    required this.onRetry,
  });

  final List<PendingSentence> failedSentences;
  final bool isRetrying;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '解析失败',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            if (failedSentences.isEmpty)
              const Text('暂无解析失败的句子。')
            else ...[
              Text('有 ${failedSentences.length} 条句子需要重试。'),
              const SizedBox(height: 8),
              for (final sentence in failedSentences.take(5))
                Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Text(
                    sentence.sourceSentence,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
            ],
            const SizedBox(height: 12),
            Align(
              alignment: Alignment.centerRight,
              child: OutlinedButton.icon(
                onPressed: onRetry,
                icon: isRetrying
                    ? const SizedBox.square(
                        dimension: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.refresh),
                label: const Text('重试解析失败项'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Splits raw import text by line and normalizes every candidate sentence.
///
/// Cleaning rules:
/// * trim leading and trailing whitespace
/// * drop empty lines
/// * merge repeated whitespace into a single ASCII space
List<String> cleanImportedSentences(String rawText) {
  final normalizedSentences = <String>[];

  for (final rawLine in rawText.split(RegExp(r'\r?\n'))) {
    final normalizedLine = rawLine.trim().replaceAll(RegExp(r'\s+'), ' ');
    if (normalizedLine.isEmpty) {
      continue;
    }

    normalizedSentences.add(normalizedLine);
  }

  return List.unmodifiable(normalizedSentences);
}
