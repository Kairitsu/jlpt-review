import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../app/router.dart';

class DictationPage extends StatelessWidget {
  const DictationPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('默写')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            Card(
              child: Padding(
                padding: const EdgeInsets.all(22),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: const [
                        Icon(Icons.graphic_eq),
                        SizedBox(width: 10),
                        Text('听音频后写出完整句子', style: TextStyle(fontWeight: FontWeight.w800)),
                      ],
                    ),
                    const SizedBox(height: 18),
                    FilledButton.tonalIcon(onPressed: null, icon: Icon(Icons.play_arrow), label: Text('播放 TTS（待接入）')),
                    const SizedBox(height: 18),
                    const TextField(
                      minLines: 6,
                      maxLines: 8,
                      decoration: InputDecoration(hintText: '在这里输入你听到的日语句子'),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            FilledButton.icon(
              onPressed: () => context.push(AppRoute.review),
              icon: const Icon(Icons.fact_check_outlined),
              label: const Text('提交并查看判定'),
            ),
          ],
        ),
      ),
    );
  }
}
