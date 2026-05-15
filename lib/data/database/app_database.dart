import 'package:drift/drift.dart';

part 'app_database.g.dart';

/// Drift database for the JLPT AI tutor local persistence layer.
///
/// The schema mirrors the product data model for imported sentences,
/// extracted vocabulary and grammar, study activity, spaced-review state, and
/// encrypted API provider configuration.
@DriftDatabase(
  tables: [
    Sentences,
    Words,
    GrammarPoints,
    StudySessions,
    StudyGroups,
    StudyRecords,
    ReviewStates,
    ApiConfigs,
  ],
)
class AppDatabase extends _$AppDatabase {
  AppDatabase(super.executor);

  @override
  int get schemaVersion => 1;
}

@DataClassName('Sentence')
@TableIndex(name: 'idx_sentences_source_sentence', columns: {#sourceSentence})
class Sentences extends Table {
  IntColumn get id => integer().autoIncrement()();

  /// Original Japanese sentence imported from AI, user input, or study content.
  TextColumn get sourceSentence => text().named('source_sentence')();
  TextColumn get translatedSentence => text().named('translated_sentence').nullable()();
  TextColumn get furigana => text().nullable()();
  TextColumn get romaji => text().nullable()();
  TextColumn get explanation => text().nullable()();
  TextColumn get jlptLevel => text().named('jlpt_level').nullable()();
  IntColumn get difficulty => integer().nullable()();
  TextColumn get sourceType => text().named('source_type').nullable()();
  TextColumn get sourceRef => text().named('source_ref').nullable()();
  TextColumn get tagsJson => text().named('tags_json').withDefault(const Constant('[]'))();
  DateTimeColumn get createdAt => dateTime().named('created_at').withDefault(currentDateAndTime)();
  DateTimeColumn get updatedAt => dateTime().named('updated_at').withDefault(currentDateAndTime)();
}

@DataClassName('Word')
class Words extends Table {
  IntColumn get id => integer().autoIncrement()();
  IntColumn get sentenceId => integer()
      .named('sentence_id')
      .nullable()
      .references(Sentences, #id, onDelete: KeyAction.setNull)();
  TextColumn get surface => text()();
  TextColumn get dictionaryForm => text().named('dictionary_form').nullable()();
  TextColumn get reading => text().nullable()();
  TextColumn get meaning => text().nullable()();
  TextColumn get partOfSpeech => text().named('part_of_speech').nullable()();
  TextColumn get jlptLevel => text().named('jlpt_level').nullable()();
  TextColumn get pitchAccent => text().named('pitch_accent').nullable()();
  TextColumn get exampleSentence => text().named('example_sentence').nullable()();
  DateTimeColumn get createdAt => dateTime().named('created_at').withDefault(currentDateAndTime)();
  DateTimeColumn get updatedAt => dateTime().named('updated_at').withDefault(currentDateAndTime)();
}

@DataClassName('GrammarPoint')
class GrammarPoints extends Table {
  IntColumn get id => integer().autoIncrement()();
  IntColumn get sentenceId => integer()
      .named('sentence_id')
      .nullable()
      .references(Sentences, #id, onDelete: KeyAction.setNull)();
  TextColumn get pattern => text()();
  TextColumn get meaning => text().nullable()();
  TextColumn get explanation => text().nullable()();
  TextColumn get jlptLevel => text().named('jlpt_level').nullable()();
  TextColumn get exampleSentence => text().named('example_sentence').nullable()();
  TextColumn get tagsJson => text().named('tags_json').withDefault(const Constant('[]'))();
  DateTimeColumn get createdAt => dateTime().named('created_at').withDefault(currentDateAndTime)();
  DateTimeColumn get updatedAt => dateTime().named('updated_at').withDefault(currentDateAndTime)();
}

@DataClassName('StudySession')
@TableIndex(name: 'idx_study_sessions_date', columns: {#date})
class StudySessions extends Table {
  IntColumn get id => integer().autoIncrement()();
  DateTimeColumn get date => dateTime()();
  DateTimeColumn get startedAt => dateTime().named('started_at').nullable()();
  DateTimeColumn get endedAt => dateTime().named('ended_at').nullable()();
  IntColumn get durationSeconds => integer().named('duration_seconds').withDefault(const Constant(0))();
  TextColumn get sessionType => text().named('session_type').nullable()();
  TextColumn get targetJlptLevel => text().named('target_jlpt_level').nullable()();
  TextColumn get note => text().nullable()();
  DateTimeColumn get createdAt => dateTime().named('created_at').withDefault(currentDateAndTime)();
  DateTimeColumn get updatedAt => dateTime().named('updated_at').withDefault(currentDateAndTime)();
}

@DataClassName('StudyGroup')
@TableIndex(name: 'idx_study_groups_session_id', columns: {#sessionId})
class StudyGroups extends Table {
  IntColumn get id => integer().autoIncrement()();
  IntColumn get sessionId => integer()
      .named('session_id')
      .references(StudySessions, #id, onDelete: KeyAction.cascade)();
  TextColumn get title => text()();
  TextColumn get groupType => text().named('group_type').nullable()();
  IntColumn get sortOrder => integer().named('sort_order').withDefault(const Constant(0))();
  DateTimeColumn get createdAt => dateTime().named('created_at').withDefault(currentDateAndTime)();
  DateTimeColumn get updatedAt => dateTime().named('updated_at').withDefault(currentDateAndTime)();
}

@DataClassName('StudyRecord')
class StudyRecords extends Table {
  IntColumn get id => integer().autoIncrement()();
  IntColumn get sessionId => integer()
      .named('session_id')
      .references(StudySessions, #id, onDelete: KeyAction.cascade)();
  IntColumn get groupId => integer()
      .named('group_id')
      .nullable()
      .references(StudyGroups, #id, onDelete: KeyAction.setNull)();
  IntColumn get sentenceId => integer()
      .named('sentence_id')
      .nullable()
      .references(Sentences, #id, onDelete: KeyAction.setNull)();
  IntColumn get wordId => integer()
      .named('word_id')
      .nullable()
      .references(Words, #id, onDelete: KeyAction.setNull)();
  IntColumn get grammarPointId => integer()
      .named('grammar_point_id')
      .nullable()
      .references(GrammarPoints, #id, onDelete: KeyAction.setNull)();
  TextColumn get itemType => text().named('item_type')();
  TextColumn get result => text().nullable()();
  IntColumn get score => integer().nullable()();
  IntColumn get durationSeconds => integer().named('duration_seconds').withDefault(const Constant(0))();
  DateTimeColumn get studiedAt => dateTime().named('studied_at').withDefault(currentDateAndTime)();
  TextColumn get note => text().nullable()();
  DateTimeColumn get createdAt => dateTime().named('created_at').withDefault(currentDateAndTime)();
}

@DataClassName('ReviewState')
@TableIndex(name: 'idx_review_states_next_review_at', columns: {#nextReviewAt})
@TableIndex(name: 'idx_review_states_review_weight', columns: {#reviewWeight})
@TableIndex(name: 'idx_review_states_item', columns: {#itemType, #itemId}, unique: true)
class ReviewStates extends Table {
  IntColumn get id => integer().autoIncrement()();
  TextColumn get itemType => text().named('item_type')();
  IntColumn get itemId => integer().named('item_id')();
  DateTimeColumn get nextReviewAt => dateTime().named('next_review_at')();
  DateTimeColumn get lastReviewedAt => dateTime().named('last_reviewed_at').nullable()();
  IntColumn get intervalDays => integer().named('interval_days').withDefault(const Constant(0))();
  RealColumn get easeFactor => real().named('ease_factor').withDefault(const Constant(2.5))();
  RealColumn get reviewWeight => real().named('review_weight').withDefault(const Constant(1.0))();
  IntColumn get repetitions => integer().withDefault(const Constant(0))();
  IntColumn get lapses => integer().withDefault(const Constant(0))();
  TextColumn get algorithmStateJson => text().named('algorithm_state_json').withDefault(const Constant('{}'))();
  DateTimeColumn get createdAt => dateTime().named('created_at').withDefault(currentDateAndTime)();
  DateTimeColumn get updatedAt => dateTime().named('updated_at').withDefault(currentDateAndTime)();
}

@DataClassName('ApiConfig')
class ApiConfigs extends Table {
  IntColumn get id => integer().autoIncrement()();
  TextColumn get provider => text()();
  TextColumn get model => text().nullable()();
  TextColumn get baseUrl => text().named('base_url').nullable()();

  /// Encrypted API key ciphertext only. Never store a raw provider API key here.
  TextColumn get apiKeyEncrypted => text().named('api_key_encrypted')();
  TextColumn get headersJson => text().named('headers_json').withDefault(const Constant('{}'))();
  BoolColumn get isActive => boolean().named('is_active').withDefault(const Constant(true))();
  DateTimeColumn get createdAt => dateTime().named('created_at').withDefault(currentDateAndTime)();
  DateTimeColumn get updatedAt => dateTime().named('updated_at').withDefault(currentDateAndTime)();
}

class SentenceRepository {
  SentenceRepository(this._db);

  final AppDatabase _db;

  Future<int> createSentence(SentencesCompanion sentence) =>
      _db.into(_db.sentences).insert(sentence);

  Future<Sentence?> findSentence(int id) =>
      (_db.select(_db.sentences)..where((tbl) => tbl.id.equals(id))).getSingleOrNull();

  Future<List<Sentence>> searchBySourceSentence(String query, {int limit = 50}) {
    return (_db.select(_db.sentences)
          ..where((tbl) => tbl.sourceSentence.contains(query))
          ..limit(limit))
        .get();
  }

  Future<List<Word>> wordsForSentence(int sentenceId) =>
      (_db.select(_db.words)..where((tbl) => tbl.sentenceId.equals(sentenceId))).get();

  Future<List<GrammarPoint>> grammarForSentence(int sentenceId) =>
      (_db.select(_db.grammarPoints)..where((tbl) => tbl.sentenceId.equals(sentenceId))).get();

  Future<bool> updateSentence(SentencesCompanion sentence) =>
      _db.update(_db.sentences).replace(sentence);

  Future<int> deleteSentence(int id) =>
      (_db.delete(_db.sentences)..where((tbl) => tbl.id.equals(id))).go();
}

class StudyRepository {
  StudyRepository(this._db);

  final AppDatabase _db;

  Future<int> createSession(StudySessionsCompanion session) =>
      _db.into(_db.studySessions).insert(session);

  Future<List<StudySession>> sessionsForDateRange(DateTime start, DateTime end) =>
      (_db.select(_db.studySessions)
            ..where((tbl) => tbl.date.isBiggerOrEqualValue(start) & tbl.date.isSmallerThanValue(end))
            ..orderBy([(tbl) => OrderingTerm.desc(tbl.date)]))
          .get();

  Future<int> createGroup(StudyGroupsCompanion group) =>
      _db.into(_db.studyGroups).insert(group);

  Future<List<StudyGroup>> groupsForSession(int sessionId) =>
      (_db.select(_db.studyGroups)
            ..where((tbl) => tbl.sessionId.equals(sessionId))
            ..orderBy([(tbl) => OrderingTerm.asc(tbl.sortOrder)]))
          .get();

  Future<int> addRecord(StudyRecordsCompanion record) =>
      _db.into(_db.studyRecords).insert(record);

  Future<List<StudyRecord>> recordsForSession(int sessionId) =>
      (_db.select(_db.studyRecords)
            ..where((tbl) => tbl.sessionId.equals(sessionId))
            ..orderBy([(tbl) => OrderingTerm.desc(tbl.studiedAt)]))
          .get();
}

class ReviewRepository {
  ReviewRepository(this._db);

  final AppDatabase _db;

  Future<List<ReviewState>> dueReviews(DateTime now, {int limit = 100}) =>
      (_db.select(_db.reviewStates)
            ..where((tbl) => tbl.nextReviewAt.isSmallerOrEqualValue(now))
            ..orderBy([
              (tbl) => OrderingTerm.asc(tbl.nextReviewAt),
              (tbl) => OrderingTerm.desc(tbl.reviewWeight),
            ])
            ..limit(limit))
          .get();

  Future<ReviewState?> findState(String itemType, int itemId) =>
      (_db.select(_db.reviewStates)
            ..where((tbl) => tbl.itemType.equals(itemType) & tbl.itemId.equals(itemId)))
          .getSingleOrNull();

  Future<int> upsertState(ReviewStatesCompanion state) =>
      _db.into(_db.reviewStates).insertOnConflictUpdate(state);

  Future<int> deleteState(int id) =>
      (_db.delete(_db.reviewStates)..where((tbl) => tbl.id.equals(id))).go();
}

class ApiConfigRepository {
  ApiConfigRepository(this._db);

  final AppDatabase _db;

  Future<int> saveEncryptedConfig({
    required String provider,
    required String apiKeyCiphertext,
    String? model,
    String? baseUrl,
    String headersJson = '{}',
    bool isActive = true,
  }) {
    _validateCiphertext(apiKeyCiphertext);

    return _db.into(_db.apiConfigs).insert(
          ApiConfigsCompanion.insert(
            provider: provider,
            apiKeyEncrypted: apiKeyCiphertext,
            model: Value(model),
            baseUrl: Value(baseUrl),
            headersJson: Value(headersJson),
            isActive: Value(isActive),
          ),
        );
  }

  Future<List<ApiConfig>> allConfigs() => _db.select(_db.apiConfigs).get();

  Future<ApiConfig?> activeConfigForProvider(String provider) =>
      (_db.select(_db.apiConfigs)
            ..where((tbl) => tbl.provider.equals(provider) & tbl.isActive.equals(true))
            ..limit(1))
          .getSingleOrNull();

  Future<int> deactivateConfig(int id) =>
      (_db.update(_db.apiConfigs)..where((tbl) => tbl.id.equals(id))).write(
        ApiConfigsCompanion(
          isActive: const Value(false),
          updatedAt: Value(DateTime.now()),
        ),
      );

  void _validateCiphertext(String ciphertext) {
    if (ciphertext.trim().isEmpty) {
      throw ArgumentError.value(ciphertext, 'apiKeyCiphertext', 'Encrypted API key ciphertext is required.');
    }

    final lower = ciphertext.toLowerCase();
    final looksLikePlaintextProviderKey = lower.startsWith('sk-') ||
        lower.startsWith('sk_') ||
        lower.startsWith('openai-') ||
        lower.startsWith('anthropic-') ||
        lower.startsWith('AIza'.toLowerCase());

    if (looksLikePlaintextProviderKey) {
      throw ArgumentError.value(
        '[redacted]',
        'apiKeyCiphertext',
        'Plaintext API keys must be encrypted before they are persisted.',
      );
    }
  }
}
