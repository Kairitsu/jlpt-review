import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../app/router.dart';
import '../../shared/mock_sentences.dart';
import '../../shared/page_shell.dart';

class LibraryPage extends StatefulWidget {
  const LibraryPage({super.key});

  @override
  State<LibraryPage> createState() => _LibraryPageState();
}

class _LibraryPageState extends State<LibraryPage> {
  String _query = '';
  JlptLevel? _level;
  ParseStatus? _status;

  @override
  Widget build(BuildContext context) {
    final sentences = importedSentences.where(_matchesFilters).toList();

    return PageShell(
      title: '句库',
      subtitle: '${sentences.length} / ${importedSentences.length} 个句子',
      actions: [
        IconButton(
          tooltip: '导入',
          onPressed: () => context.push(AppRoute.import),
          icon: const Icon(Icons.upload_file_outlined),
        ),
      ],
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TextField(
            onChanged: (value) => setState(() => _query = value.trim().toLowerCase()),
            decoration: const InputDecoration(
              hintText: '搜索日文、读音、译文或标签',
              prefixIcon: Icon(Icons.search),
            ),
          ),
          const SizedBox(height: 14),
          _FilterChips<JlptLevel>(
            label: 'JLPT',
            values: JlptLevel.values,
            selected: _level,
            labelBuilder: (level) => level.label,
            onSelected: (level) => setState(() => _level = _level == level ? null : level),
          ),
          const SizedBox(height: 8),
          _FilterChips<ParseStatus>(
            label: '解析状态',
            values: ParseStatus.values,
            selected: _status,
            labelBuilder: (status) => status.label,
            onSelected: (status) => setState(() => _status = _status == status ? null : status),
          ),
          const SizedBox(height: 18),
          if (sentences.isEmpty)
            const _EmptyLibraryState()
          else
            ...sentences.map((sentence) => Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: _SentenceCard(sentence: sentence),
                )),
        ],
      ),
    );
  }

  bool _matchesFilters(SentenceItem sentence) {
    final searchable = [
      sentence.japanese,
      sentence.reading,
      sentence.translation,
      ...sentence.tags,
    ].join(' ').toLowerCase();

    return (_query.isEmpty || searchable.contains(_query)) &&
        (_level == null || sentence.level == _level) &&
        (_status == null || sentence.status == _status);
  }
}

class _FilterChips<T> extends StatelessWidget {
  const _FilterChips({
    required this.label,
    required this.values,
    required this.selected,
    required this.labelBuilder,
    required this.onSelected,
  });

  final String label;
  final List<T> values;
  final T? selected;
  final String Function(T value) labelBuilder;
  final ValueChanged<T> onSelected;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: Theme.of(context).textTheme.labelLarge),
        const SizedBox(height: 6),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: values
              .map(
                (value) => FilterChip(
                  label: Text(labelBuilder(value)),
                  selected: selected == value,
                  onSelected: (_) => onSelected(value),
                ),
              )
              .toList(),
        ),
      ],
    );
  }
}

class _SentenceCard extends StatelessWidget {
  const _SentenceCard({required this.sentence});

  final SentenceItem sentence;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Chip(label: Text(sentence.level.label)),
                const SizedBox(width: 8),
                Chip(label: Text(sentence.status.label)),
              ],
            ),
            const SizedBox(height: 10),
            Text(sentence.japanese, style: Theme.of(context).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w800)),
            const SizedBox(height: 6),
            Text(sentence.reading, style: Theme.of(context).textTheme.bodyMedium),
            const SizedBox(height: 10),
            Text(sentence.translation),
            const SizedBox(height: 12),
            Wrap(
              spacing: 6,
              children: sentence.tags.map((tag) => Text('#$tag')).toList(),
            ),
          ],
        ),
      ),
    );
  }
}

class _EmptyLibraryState extends StatelessWidget {
  const _EmptyLibraryState();

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          children: const [
            Icon(Icons.search_off, size: 48),
            SizedBox(height: 12),
            Text('没有符合条件的句子'),
            Text('尝试清空搜索或切换筛选条件。'),
          ],
        ),
      ),
    );
  }
}
