import 'provider_config.dart';
import 'provider_config_repository.dart';

/// Lightweight database adapter for tests, demos, and early app wiring.
final class InMemoryProviderConfigDatabase implements ProviderConfigDatabase {
  final Map<String, ProviderConfig> _configs = <String, ProviderConfig>{};

  @override
  Future<void> upsertProviderConfig(ProviderConfig config) async {
    _configs[config.id] = config;
  }

  @override
  Future<ProviderConfig?> readProviderConfig(String id) async {
    return _configs[id];
  }

  @override
  Future<void> deleteProviderApiKey(String id) async {
    final config = _configs[id];
    if (config == null) {
      return;
    }
    _configs[id] = config.copyWith(
      clearApiKey: true,
      updatedAt: DateTime.now().toUtc(),
    );
  }
}
