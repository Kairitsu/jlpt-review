import 'package:flutter/material.dart';

import '../../shared/page_shell.dart';

class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key});

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  double _dailyNew = 8;
  double _groupSize = 5;
  double _strictness = .72;

  @override
  Widget build(BuildContext context) {
    return PageShell(
      title: '设置',
      subtitle: '模型、语音与学习节奏',
      child: Column(
        children: [
          const _ConfigCard(
            title: 'LLM API 配置',
            icon: Icons.psychology_alt_outlined,
            fields: ['Base URL', 'API Key', '模型名称'],
          ),
          const SizedBox(height: 14),
          const _ConfigCard(
            title: 'TTS API 配置',
            icon: Icons.record_voice_over_outlined,
            fields: ['Base URL', 'API Key', '声音 / Voice ID'],
          ),
          const SizedBox(height: 14),
          _SliderCard(
            title: '每日新学数量',
            valueLabel: _dailyNew.round().toString(),
            value: _dailyNew,
            min: 0,
            max: 30,
            divisions: 30,
            onChanged: (value) => setState(() => _dailyNew = value),
          ),
          const SizedBox(height: 14),
          _SliderCard(
            title: '每组句子数量',
            valueLabel: _groupSize.round().toString(),
            value: _groupSize,
            min: 3,
            max: 12,
            divisions: 9,
            onChanged: (value) => setState(() => _groupSize = value),
          ),
          const SizedBox(height: 14),
          _SliderCard(
            title: '默写判定严格度',
            valueLabel: '${(_strictness * 100).round()}%',
            value: _strictness,
            min: .4,
            max: 1,
            divisions: 12,
            onChanged: (value) => setState(() => _strictness = value),
          ),
        ],
      ),
    );
  }
}

class _ConfigCard extends StatelessWidget {
  const _ConfigCard({required this.title, required this.icon, required this.fields});

  final String title;
  final IconData icon;
  final List<String> fields;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [Icon(icon), const SizedBox(width: 10), Text(title, style: const TextStyle(fontWeight: FontWeight.w900, fontSize: 18))]),
            const SizedBox(height: 16),
            ...fields.map(
              (field) => Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: TextField(
                  obscureText: field.toLowerCase().contains('key'),
                  decoration: InputDecoration(labelText: field),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _SliderCard extends StatelessWidget {
  const _SliderCard({
    required this.title,
    required this.valueLabel,
    required this.value,
    required this.min,
    required this.max,
    required this.divisions,
    required this.onChanged,
  });

  final String title;
  final String valueLabel;
  final double value;
  final double min;
  final double max;
  final int divisions;
  final ValueChanged<double> onChanged;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(title, style: const TextStyle(fontWeight: FontWeight.w800)),
                Text(valueLabel, style: Theme.of(context).textTheme.titleLarge),
              ],
            ),
            Slider(value: value, min: min, max: max, divisions: divisions, onChanged: onChanged),
          ],
        ),
      ),
    );
  }
}
