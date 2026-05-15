import '../../core/security/api_key_cipher.dart';
import '../../core/security/api_key_redactor.dart';
import 'provider_config.dart';

/// Minimal database adapter for provider settings.
///
/// Implementations can use SQLite, Drift, Isar, or another database as long as
/// they persist [ProviderConfig.apiKeyEncrypted] to the `api_key_encrypted`
/// column and never receive plaintext API keys.
abstract interface class ProviderConfigDatabase {
  Future<void> upsertProviderConfig(ProviderConfig config);

  Future<ProviderConfig?> readProviderConfig(String id);

  Future<void> deleteProviderApiKey(String id);
}

/// Repository that enforces encryption at the persistence boundary.
final class ProviderConfigRepository {
  ProviderConfigRepository({
    required ProviderConfigDatabase database,
    ApiKeyCipher? apiKeyCipher,
  })  : _database = database,
        _apiKeyCipher = apiKeyCipher ?? ApiKeyCipher();

  final ProviderConfigDatabase _database;
  final ApiKeyCipher _apiKeyCipher;

  /// Saves LLM or TTS settings. [plainTextApiKey] is used only as a temporary
  /// input and is converted to [ProviderConfig.apiKeyEncrypted] before writing.
  ///
  /// When [plainTextApiKey] is null, the existing encrypted key is preserved so
  /// users can edit non-secret settings without re-entering a credential. Use
  /// [deleteProviderApiKey] for explicit removal.
  Future<void> saveProviderConfig({
    required ProviderConfig config,
    String? plainTextApiKey,
  }) async {
    final existing = await _database.readProviderConfig(config.id);
    final encryptedApiKey = plainTextApiKey == null
        ? existing?.apiKeyEncrypted
        : await _apiKeyCipher.encryptNullable(plainTextApiKey);
    final databaseConfig = config.copyWith(
      apiKeyEncrypted: encryptedApiKey,
      updatedAt: DateTime.now().toUtc(),
    );
    await _database.upsertProviderConfig(databaseConfig);
  }

  Future<ProviderConfig?> readProviderConfig(String id) {
    return _database.readProviderConfig(id);
  }

  /// Returns plaintext only for the short-lived provider call path.
  Future<ProviderRuntimeConfig?> readRuntimeConfig(String id) async {
    final config = await _database.readProviderConfig(id);
    if (config == null) {
      return null;
    }

    final plainTextApiKey = await _apiKeyCipher.decryptNullable(
      config.apiKeyEncrypted,
    );
    return ProviderRuntimeConfig(config: config, apiKey: plainTextApiKey);
  }

  Future<void> deleteProviderApiKey(String id) {
    return _database.deleteProviderApiKey(id);
  }

  String redactApiKey(String? apiKey, {bool showEdges = true}) {
    return ApiKeyRedactor.redact(apiKey, showEdges: showEdges);
  }
}
