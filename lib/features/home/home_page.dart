import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../app/router.dart';
import '../../shared/page_shell.dart';

class HomePage extends StatelessWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context) {
    return PageShell(
      title: '今日练习',
      subtitle: 'AI 辅助 JLPT 句子训练',
      actions: [
        IconButton(
          tooltip: '导入句子',
          onPressed: () => context.push(AppRoute.import),
          icon: const Icon(Icons.add_circle_outline),
        ),
      ],
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const _HeroProgressCard(),
          const SizedBox(height: 18),
          Row(
            children: const [
              Expanded(child: _MetricTile(label: '今日复习', value: '18', tone: Color(0xFF4056A1))),
              SizedBox(width: 12),
              Expanded(child: _MetricTile(label: '今日新学', value: '8', tone: Color(0xFFFF6F91))),
            ],
          ),
          const SizedBox(height: 18),
          FilledButton.icon(
            onPressed: () => context.push(AppRoute.study),
            icon: const Icon(Icons.play_arrow_rounded),
            label: const Text('开始学习'),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: () => context.go(AppRoute.library),
                  icon: const Icon(Icons.menu_book_outlined),
                  label: const Text('句库'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: () => context.go(AppRoute.settings),
                  icon: const Icon(Icons.settings_outlined),
                  label: const Text('设置'),
                ),
              ),
            ],
          ),
          const SizedBox(height: 18),
          const _StudyPathCard(),
        ],
      ),
    );
  }
}

class _HeroProgressCard extends StatelessWidget {
  const _HeroProgressCard();

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('当前完成进度', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 16),
            Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Text('64%', style: Theme.of(context).textTheme.displayMedium?.copyWith(fontWeight: FontWeight.w900)),
                const SizedBox(width: 12),
                const Padding(
                  padding: EdgeInsets.only(bottom: 10),
                  child: Text('N3 句型冲刺'),
                ),
              ],
            ),
            const SizedBox(height: 18),
            ClipRRect(
              borderRadius: BorderRadius.circular(99),
              child: const LinearProgressIndicator(value: .64, minHeight: 12),
            ),
          ],
        ),
      ),
    );
  }
}

class _MetricTile extends StatelessWidget {
  const _MetricTile({required this.label, required this.value, required this.tone});

  final String label;
  final String value;
  final Color tone;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(Icons.auto_stories, color: tone),
            const SizedBox(height: 14),
            Text(value, style: Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w900)),
            Text(label, style: Theme.of(context).textTheme.bodyMedium),
          ],
        ),
      ),
    );
  }
}

class _StudyPathCard extends StatelessWidget {
  const _StudyPathCard();

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: const [
            Text('今日路径', style: TextStyle(fontWeight: FontWeight.w800, fontSize: 18)),
            SizedBox(height: 12),
            _PathStep(icon: Icons.hearing_outlined, text: '听句子并理解语境'),
            _PathStep(icon: Icons.edit_note, text: '默写关键表达'),
            _PathStep(icon: Icons.refresh, text: '复习错题并完成打卡'),
          ],
        ),
      ),
    );
  }
}

class _PathStep extends StatelessWidget {
  const _PathStep({required this.icon, required this.text});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(children: [Icon(icon), const SizedBox(width: 12), Expanded(child: Text(text))]),
    );
  }
}
