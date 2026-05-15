import 'dart:convert';
import 'dart:math';

import 'package:cryptography/cryptography.dart';

import 'secret_storage_service.dart';

/// Encrypts and decrypts API keys before they cross the database boundary.
final class ApiKeyCipher {
  ApiKeyCipher({SecretStorageService? secretStorageService})
      : _secretStorageService = secretStorageService ?? SecretStorageService();

  static const String payloadPrefix = 'ak1:';

  final SecretStorageService _secretStorageService;
  final AesGcm _algorithm = AesGcm.with256bits();

  bool isEncrypted(String? value) {
    return value != null && value.startsWith(payloadPrefix);
  }

  Future<String?> encryptNullable(String? apiKey) async {
    if (apiKey == null || apiKey.isEmpty) {
      return null;
    }
    return encrypt(apiKey);
  }

  Future<String> encrypt(String apiKey) async {
    final encryptionKey =
        await _secretStorageService.readOrCreateApiKeyEncryptionKey();
    final secretKey = SecretKey(encryptionKey);
    final nonce = _randomBytes(12);
    final secretBox = await _algorithm.encrypt(
      utf8.encode(apiKey),
      secretKey: secretKey,
      nonce: nonce,
    );

    final payload = <String, String>{
      'nonce': base64Url.encode(secretBox.nonce),
      'cipherText': base64Url.encode(secretBox.cipherText),
      'mac': base64Url.encode(secretBox.mac.bytes),
    };

    final encodedPayload = base64Url.encode(utf8.encode(jsonEncode(payload)));
    return '$payloadPrefix$encodedPayload';
  }

  Future<String?> decryptNullable(String? encryptedApiKey) async {
    if (encryptedApiKey == null || encryptedApiKey.isEmpty) {
      return null;
    }
    return decrypt(encryptedApiKey);
  }

  Future<String> decrypt(String encryptedApiKey) async {
    if (!isEncrypted(encryptedApiKey)) {
      throw const FormatException('API key payload is not encrypted.');
    }

    final payloadJson = utf8.decode(
      base64Url.decode(encryptedApiKey.substring(payloadPrefix.length)),
    );
    final payload = (jsonDecode(payloadJson) as Map<String, dynamic>)
        .cast<String, String>();
    final encryptionKey =
        await _secretStorageService.readOrCreateApiKeyEncryptionKey();
    final secretBox = SecretBox(
      base64Url.decode(payload['cipherText']!),
      nonce: base64Url.decode(payload['nonce']!),
      mac: Mac(base64Url.decode(payload['mac']!)),
    );
    final plainTextBytes = await _algorithm.decrypt(
      secretBox,
      secretKey: SecretKey(encryptionKey),
    );

    return utf8.decode(plainTextBytes);
  }

  List<int> _randomBytes(int length) {
    final random = Random.secure();
    return List<int>.generate(length, (_) => random.nextInt(256));
  }
}
