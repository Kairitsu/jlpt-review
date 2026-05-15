import 'package:flutter/material.dart';
import '../../app/router.dart';

class StudyPage extends StatelessWidget {
  const StudyPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('学习')),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Expanded(
                child: Card(
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Chip(label: Text('N3 · 新学')),
                        const Spacer(),
                        Text('会議に遅れないように、早めに出発します。', style: Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w900)),
                        const SizedBox(height: 18),
                        const Text('为了不迟到，我会早点出发。'),
                        const Spacer(),
                        const _InsightLine(icon: Icons.lightbulb_outline, text: 'ように：表示目的或期望达成的状态。'),
                        const _InsightLine(icon: Icons.volume_up_outlined, text: '先听 TTS，再遮住译文复述。'),
                      ],
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 16),
              FilledButton.icon(
                onPressed: () => Navigator.of(context).pushNamed(AppRoute.dictation),
                icon: const Icon(Icons.edit_note),
                label: const Text('进入默写'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _InsightLine extends StatelessWidget {
  const _InsightLine({required this.icon, required this.text});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 10),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [Icon(icon), const SizedBox(width: 10), Expanded(child: Text(text))]),
    );
  }
}
