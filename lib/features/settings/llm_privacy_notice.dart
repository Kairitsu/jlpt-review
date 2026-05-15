import 'package:flutter/material.dart';

import 'provider_settings_controller.dart';

/// Notice shown before LLM requests are made from user-entered sentence text.
class LlmPrivacyNotice extends StatelessWidget {
  const LlmPrivacyNotice({super.key});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Icon(Icons.info_outline, color: theme.colorScheme.primary),
            const SizedBox(width: 12),
            const Expanded(
              child: Text(ProviderSettingsController.llmRequestNotice),
            ),
          ],
        ),
      ),
    );
  }
}
