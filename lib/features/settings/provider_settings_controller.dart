import '../../data/database/provider_config.dart';
import '../../data/database/provider_config_repository.dart';

/// Coordinates settings-page interactions for LLM and TTS providers.
final class ProviderSettingsController {
  ProviderSettingsController({required ProviderConfigRepository repository})
      : _repository = repository;

  static const String llmRequestNotice = '句子内容会发送到用户配置的模型服务。';

  final ProviderConfigRepository _repository;

  Future<void> saveLlmConfig({
    required String id,
    required String providerName,
    required String baseUrl,
    required String model,
    String? plainTextApiKey,
  }) {
    return _saveConfig(
      id: id,
      type: ProviderConfigType.llm,
      providerName: providerName,
      baseUrl: baseUrl,
      model: model,
      plainTextApiKey: plainTextApiKey,
    );
  }

  Future<void> saveTtsConfig({
    required String id,
    required String providerName,
    required String baseUrl,
    required String model,
    String? plainTextApiKey,
  }) {
    return _saveConfig(
      id: id,
      type: ProviderConfigType.tts,
      providerName: providerName,
      baseUrl: baseUrl,
      model: model,
      plainTextApiKey: plainTextApiKey,
    );
  }

  Future<ProviderRuntimeConfig?> readConfigForProviderCall(String id) {
    return _repository.readRuntimeConfig(id);
  }

  Future<void> deleteApiKey(String id) {
    return _repository.deleteProviderApiKey(id);
  }

  String safeApiKeyLabel(String? apiKey) {
    return _repository.redactApiKey(apiKey);
  }

  Future<void> _saveConfig({
    required String id,
    required ProviderConfigType type,
    required String providerName,
    required String baseUrl,
    required String model,
    String? plainTextApiKey,
  }) {
    final config = ProviderConfig(
      id: id,
      type: type,
      providerName: providerName,
      baseUrl: baseUrl,
      model: model,
    );
    return _repository.saveProviderConfig(
      config: config,
      plainTextApiKey: plainTextApiKey,
    );
  }
}
