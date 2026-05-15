import 'package:flutter/material.dart';

class ImportPage extends StatelessWidget {
  const ImportPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('导入句子')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            Card(
              child: Padding(
                padding: const EdgeInsets.all(20),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('批量粘贴', style: Theme.of(context).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w800)),
                    const SizedBox(height: 8),
                    const Text('每行一个日语句子，AI 将补充读音、译文、语法点和 JLPT 等级。'),
                    const SizedBox(height: 18),
                    const TextField(
                      minLines: 8,
                      maxLines: 12,
                      decoration: InputDecoration(
                        hintText: '例：\n明日は雨が降るかもしれません。\n会議に遅れないように、早めに出発します。',
                        alignLabelWithHint: true,
                      ),
                    ),
                    const SizedBox(height: 18),
                    FilledButton.icon(
                      onPressed: null,
                      icon: Icon(Icons.auto_fix_high),
                      label: Text('解析并导入（待接入）'),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 14),
            const _ImportOption(icon: Icons.description_outlined, title: '文件导入', subtitle: '支持后续接入 CSV、TXT 或 Anki 导出文件。'),
            const SizedBox(height: 12),
            const _ImportOption(icon: Icons.link_outlined, title: '网页摘录', subtitle: '预留文章链接解析入口，适合阅读材料沉淀。'),
          ],
        ),
      ),
    );
  }
}

class _ImportOption extends StatelessWidget {
  const _ImportOption({required this.icon, required this.title, required this.subtitle});

  final IconData icon;
  final String title;
  final String subtitle;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: ListTile(
        contentPadding: const EdgeInsets.all(16),
        leading: Icon(icon),
        title: Text(title, style: const TextStyle(fontWeight: FontWeight.w800)),
        subtitle: Text(subtitle),
        trailing: const Icon(Icons.chevron_right),
      ),
    );
  }
}
