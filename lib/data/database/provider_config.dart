/// Provider categories that can require user-supplied API keys.
enum ProviderConfigType { llm, tts }

/// Database-facing provider configuration.
///
/// API keys are intentionally represented only by [apiKeyEncrypted]. Plaintext
/// keys should live in temporary local variables during save or request flows.
final class ProviderConfig {
  const ProviderConfig({
    required this.id,
    required this.type,
    required this.providerName,
    required this.baseUrl,
    required this.model,
    this.apiKeyEncrypted,
    this.updatedAt,
  });

  final String id;
  final ProviderConfigType type;
  final String providerName;
  final String baseUrl;
  final String model;
  final String? apiKeyEncrypted;
  final DateTime? updatedAt;

  ProviderConfig copyWith({
    String? id,
    ProviderConfigType? type,
    String? providerName,
    String? baseUrl,
    String? model,
    String? apiKeyEncrypted,
    bool clearApiKey = false,
    DateTime? updatedAt,
  }) {
    return ProviderConfig(
      id: id ?? this.id,
      type: type ?? this.type,
      providerName: providerName ?? this.providerName,
      baseUrl: baseUrl ?? this.baseUrl,
      model: model ?? this.model,
      apiKeyEncrypted: clearApiKey
          ? null
          : apiKeyEncrypted ?? this.apiKeyEncrypted,
      updatedAt: updatedAt ?? this.updatedAt,
    );
  }

  Map<String, Object?> toDatabaseMap() {
    return <String, Object?>{
      'id': id,
      'type': type.name,
      'provider_name': providerName,
      'base_url': baseUrl,
      'model': model,
      'api_key_encrypted': apiKeyEncrypted,
      'updated_at': updatedAt?.toIso8601String(),
    };
  }

  static ProviderConfig fromDatabaseMap(Map<String, Object?> map) {
    return ProviderConfig(
      id: map['id']! as String,
      type: ProviderConfigType.values.byName(map['type']! as String),
      providerName: map['provider_name']! as String,
      baseUrl: map['base_url']! as String,
      model: map['model']! as String,
      apiKeyEncrypted: map['api_key_encrypted'] as String?,
      updatedAt: map['updated_at'] == null
          ? null
          : DateTime.parse(map['updated_at']! as String),
    );
  }
}

/// Runtime-only configuration used immediately before calling a provider API.
///
/// Do not persist this object or include it in debug output because [apiKey]
/// contains plaintext.
final class ProviderRuntimeConfig {
  const ProviderRuntimeConfig({
    required this.config,
    required this.apiKey,
  });

  final ProviderConfig config;
  final String? apiKey;
}
