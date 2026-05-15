import 'package:flutter/material.dart';
import '../../app/router.dart';

class ReviewPage extends StatelessWidget {
  const ReviewPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('复盘')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            Card(
              child: Padding(
                padding: const EdgeInsets.all(22),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: const [
                    Text('判定结果', style: TextStyle(fontSize: 22, fontWeight: FontWeight.w900)),
                    SizedBox(height: 12),
                    LinearProgressIndicator(value: .86, minHeight: 10),
                    SizedBox(height: 12),
                    Text('相似度 86% · 通过'),
                    SizedBox(height: 18),
                    _ReviewRow(label: '原句', text: '会議に遅れないように、早めに出発します。'),
                    _ReviewRow(label: '你的答案', text: '会議に遅れないように、早く出発します。'),
                    _ReviewRow(label: '提示', text: '早めに 更强调“提前一点”的安排。'),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            FilledButton.icon(
              onPressed: () => Navigator.of(context).pushNamed(AppRoute.completion),
              icon: const Icon(Icons.check_circle_outline),
              label: const Text('完成本组'),
            ),
          ],
        ),
      ),
    );
  }
}

class _ReviewRow extends StatelessWidget {
  const _ReviewRow({required this.label, required this.text});

  final String label;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: const TextStyle(fontWeight: FontWeight.w800)),
          const SizedBox(height: 4),
          Text(text),
        ],
      ),
    );
  }
}
