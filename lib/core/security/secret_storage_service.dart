import 'dart:convert';
import 'dart:math';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Stores secrets that must never be persisted in application databases.
///
/// The service keeps the database encryption key in platform secure storage
/// (Keychain, Keystore, libsecret, or the browser's secure storage backend as
/// implemented by `flutter_secure_storage`). Database rows only receive encrypted
/// API-key payloads derived from this key.
final class SecretStorageService {
  SecretStorageService({FlutterSecureStorage? secureStorage})
      : _secureStorage = secureStorage ?? const FlutterSecureStorage();

  static const String apiKeyEncryptionKeyName = 'api_key_encryption_key_v1';

  final FlutterSecureStorage _secureStorage;

  Future<String?> readSecret(String key) => _secureStorage.read(key: key);

  Future<void> writeSecret(String key, String value) {
    return _secureStorage.write(key: key, value: value);
  }

  Future<void> deleteSecret(String key) => _secureStorage.delete(key: key);

  /// Returns the 256-bit API-key encryption key, creating it on first use.
  Future<List<int>> readOrCreateApiKeyEncryptionKey() async {
    final existing = await readSecret(apiKeyEncryptionKeyName);
    if (existing != null && existing.isNotEmpty) {
      return base64Url.decode(existing);
    }

    final key = _randomBytes(32);
    await writeSecret(apiKeyEncryptionKeyName, base64Url.encode(key));
    return key;
  }

  Future<void> deleteApiKeyEncryptionKey() {
    return deleteSecret(apiKeyEncryptionKeyName);
  }

  List<int> _randomBytes(int length) {
    final random = Random.secure();
    return List<int>.generate(length, (_) => random.nextInt(256));
  }
}
