import 'package:flutter/material.dart';
import '../../app/router.dart';

class CompletionPage extends StatelessWidget {
  const CompletionPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Spacer(),
              const Icon(Icons.celebration_outlined, size: 86),
              const SizedBox(height: 18),
              Text('今日目标完成', textAlign: TextAlign.center, style: Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w900)),
              const SizedBox(height: 10),
              const Text('已完成 8 个新学句子和 18 个复习句子。明天会根据掌握情况自动安排间隔复习。', textAlign: TextAlign.center),
              const Spacer(),
              FilledButton.icon(
                onPressed: () => Navigator.of(context).pushNamedAndRemoveUntil(AppRoute.home, (route) => false),
                icon: const Icon(Icons.home_outlined),
                label: const Text('返回首页'),
              ),
              const SizedBox(height: 12),
              OutlinedButton.icon(
                onPressed: () => Navigator.of(context).pushNamed(AppRoute.library),
                icon: const Icon(Icons.menu_book_outlined),
                label: const Text('查看句库'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
