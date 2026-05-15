import 'package:flutter/material.dart';

import '../features/completion/completion_page.dart';
import '../features/dictation/dictation_page.dart';
import '../features/home/home_page.dart';
import '../features/import/import_page.dart';
import '../features/library/library_page.dart';
import '../features/review/review_page.dart';
import '../features/settings/settings_page.dart';
import '../features/study/study_page.dart';

class AppRoute {
  const AppRoute._();

  static const home = '/';
  static const library = '/library';
  static const import = '/import';
  static const settings = '/settings';
  static const study = '/study';
  static const dictation = '/dictation';
  static const review = '/review';
  static const completion = '/completion';
}

Route<void> onGenerateAppRoute(RouteSettings settings) {
  final page = switch (settings.name) {
    AppRoute.home => const HomePage(),
    AppRoute.library => const LibraryPage(),
    AppRoute.import => const ImportPage(),
    AppRoute.settings => const SettingsPage(),
    AppRoute.study => const StudyPage(),
    AppRoute.dictation => const DictationPage(),
    AppRoute.review => const ReviewPage(),
    AppRoute.completion => const CompletionPage(),
    _ => const HomePage(),
  };

  return PageRouteBuilder<void>(
    settings: settings,
    pageBuilder: (context, animation, secondaryAnimation) => page,
    transitionsBuilder: (context, animation, secondaryAnimation, child) {
      return FadeTransition(
        opacity: CurvedAnimation(parent: animation, curve: Curves.easeOutCubic),
        child: child,
      );
    },
  );
}
