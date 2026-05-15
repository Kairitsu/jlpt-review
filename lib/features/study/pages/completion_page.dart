import 'package:flutter/material.dart';

import '../services/daily_stats_service.dart';

/// Summary screen displayed after the learner finishes every item in today's
/// plan.
class CompletionPage extends StatelessWidget {
  const CompletionPage({
    super.key,
    required this.stats,
    this.onContinue,
  });

  static const routeName = '/study/completion';

  final DailyStudyStats stats;
  final VoidCallback? onContinue;

  static Future<T?> open<T>(
    BuildContext context, {
    required DailyStudyStats stats,
  }) {
    return Navigator.of(context).pushReplacement<T, Object?>(
      MaterialPageRoute(
        settings: const RouteSettings(name: routeName),
        builder: (_) => CompletionPage(stats: stats),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(
        title: const Text('今日学习完成'),
        automaticallyImplyLeading: false,
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Icon(
                Icons.celebration_rounded,
                size: 64,
                color: theme.colorScheme.primary,
              ),
              const SizedBox(height: 16),
              Text(
                '计划已全部完成',
                textAlign: TextAlign.center,
                style: theme.textTheme.headlineSmall?.copyWith(
                  fontWeight: FontWeight.bold,
                ),
              ),
              const SizedBox(height: 8),
              Text(
                '辛苦了！下面是今天的学习统计。',
                textAlign: TextAlign.center,
                style: theme.textTheme.bodyMedium,
              ),
              const SizedBox(height: 24),
              Expanded(
                child: ListView(
                  children: [
                    _StatTile(
                      icon: Icons.auto_stories_rounded,
                      label: '今日新学句数',
                      value: '${stats.todayNewSentenceCount}',
                    ),
                    _StatTile(
                      icon: Icons.replay_rounded,
                      label: '今日复习句数',
                      value: '${stats.todayReviewSentenceCount}',
                    ),
                    _StatTile(
                      icon: Icons.edit_note_rounded,
                      label: '默写正确率',
                      value: '${stats.dictationAccuracyPercent}%',
                      subtitle:
                          '${stats.dictationCorrectCount}/${stats.dictationSubmissionCount} 次正确',
                    ),
                    _StatTile(
                      icon: Icons.error_outline_rounded,
                      label: '错题数量',
                      value: '${stats.wrongSentenceCount}',
                    ),
                    _StatTile(
                      icon: Icons.event_available_rounded,
                      label: '明日预计复习数量',
                      value: '${stats.tomorrowExpectedReviewCount}',
                    ),
                    _StatTile(
                      icon: Icons.local_fire_department_rounded,
                      label: '连续学习天数',
                      value: '${stats.studyStreakDays}',
                      subtitle: '保持节奏，明天继续加油',
                    ),
                  ],
                ),
              ),
              FilledButton(
                onPressed: onContinue ?? () => Navigator.of(context).pop(),
                child: const Text('完成'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _StatTile extends StatelessWidget {
  const _StatTile({
    required this.icon,
    required this.label,
    required this.value,
    this.subtitle,
  });

  final IconData icon;
  final String label;
  final String value;
  final String? subtitle;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: ListTile(
        leading: Icon(icon, color: theme.colorScheme.primary),
        title: Text(label),
        subtitle: subtitle == null ? null : Text(subtitle!),
        trailing: Text(
          value,
          style: theme.textTheme.titleLarge?.copyWith(
            fontWeight: FontWeight.bold,
          ),
        ),
      ),
    );
  }
}
