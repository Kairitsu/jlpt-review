/// Runtime configuration used by LLM providers.
class ApiConfig {
  const ApiConfig({
    required this.providerName,
    required this.baseUrl,
    required this.encryptedApiKey,
    required this.modelName,
    required this.temperature,
    required this.maxTokens,
    required this.timeoutSeconds,
  });

  final String providerName;
  final String baseUrl;
  final String encryptedApiKey;
  final String modelName;
  final double temperature;
  final int maxTokens;
  final int timeoutSeconds;
}

/// Reads persisted API configuration for the currently selected LLM provider.
abstract class ApiConfigRepository {
  Future<ApiConfig> getConfig();
}

/// Decrypts an API key immediately before it is sent to the provider.
abstract class ApiKeyDecrypter {
  Future<String> decrypt(String encryptedApiKey);
}
