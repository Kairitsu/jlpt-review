/// Utilities for preventing API keys and other secrets from leaking into logs,
/// exception messages, or debug output.
final class ApiKeyRedactor {
  const ApiKeyRedactor._();

  static const String redacted = '***';

  /// Redacts a single secret. When [showEdges] is true, only the first and last
  /// four characters are shown for easier user recognition.
  static String redact(String? secret, {bool showEdges = true}) {
    if (secret == null || secret.isEmpty) {
      return redacted;
    }

    if (!showEdges || secret.length <= 8) {
      return redacted;
    }

    return '${secret.substring(0, 4)}$redacted${secret.substring(secret.length - 4)}';
  }

  /// Replaces all provided [secrets] in [message] with safe redacted values.
  static String redactMessage(
    Object? message, {
    Iterable<String?> secrets = const <String?>[],
    bool showEdges = true,
  }) {
    var safeMessage = message?.toString() ?? '';
    for (final secret in secrets) {
      if (secret == null || secret.isEmpty) {
        continue;
      }
      safeMessage = safeMessage.replaceAll(
        secret,
        redact(secret, showEdges: showEdges),
      );
    }
    return safeMessage;
  }
}
