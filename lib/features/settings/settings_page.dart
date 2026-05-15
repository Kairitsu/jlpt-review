import 'package:flutter/material.dart';

import 'llm_privacy_notice.dart';
import 'provider_settings_controller.dart';

/// Settings UI fragment for managing provider API keys securely.
class SettingsPage extends StatelessWidget {
  const SettingsPage({
    required ProviderSettingsController controller,
    String llmProviderId = 'llm',
    String ttsProviderId = 'tts',
    super.key,
  })  : _controller = controller,
        _llmProviderId = llmProviderId,
        _ttsProviderId = ttsProviderId;

  final ProviderSettingsController _controller;
  final String _llmProviderId;
  final String _ttsProviderId;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: <Widget>[
          const LlmPrivacyNotice(),
          const SizedBox(height: 16),
          _ApiKeyDeleteTile(
            title: 'Delete LLM API Key',
            subtitle: 'Remove the encrypted LLM credential from this device.',
            onDelete: () => _deleteApiKey(context, _llmProviderId),
          ),
          const Divider(),
          _ApiKeyDeleteTile(
            title: 'Delete TTS API Key',
            subtitle: 'Remove the encrypted TTS credential from this device.',
            onDelete: () => _deleteApiKey(context, _ttsProviderId),
          ),
        ],
      ),
    );
  }

  Future<void> _deleteApiKey(BuildContext context, String providerId) async {
    await _controller.deleteApiKey(providerId);
    if (!context.mounted) {
      return;
    }
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('API key deleted.')),
    );
  }
}

class _ApiKeyDeleteTile extends StatelessWidget {
  const _ApiKeyDeleteTile({
    required this.title,
    required this.subtitle,
    required this.onDelete,
  });

  final String title;
  final String subtitle;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      title: Text(title),
      subtitle: Text(subtitle),
      trailing: FilledButton.tonalIcon(
        onPressed: onDelete,
        icon: const Icon(Icons.delete_outline),
        label: const Text('Delete'),
      ),
    );
  }
}
