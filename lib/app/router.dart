import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

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

final appRouter = GoRouter(
  initialLocation: AppRoute.home,
  routes: [
    GoRoute(
      path: AppRoute.home,
      pageBuilder: (context, state) => _fadePage(state, const HomePage()),
    ),
    GoRoute(
      path: AppRoute.library,
      pageBuilder: (context, state) => _fadePage(state, const LibraryPage()),
    ),
    GoRoute(
      path: AppRoute.import,
      pageBuilder: (context, state) => _fadePage(state, const ImportPage()),
    ),
    GoRoute(
      path: AppRoute.settings,
      pageBuilder: (context, state) => _fadePage(state, const SettingsPage()),
    ),
    GoRoute(
      path: AppRoute.study,
      pageBuilder: (context, state) => _fadePage(state, const StudyPage()),
    ),
    GoRoute(
      path: AppRoute.dictation,
      pageBuilder: (context, state) => _fadePage(state, const DictationPage()),
    ),
    GoRoute(
      path: AppRoute.review,
      pageBuilder: (context, state) => _fadePage(state, const ReviewPage()),
    ),
    GoRoute(
      path: AppRoute.completion,
      pageBuilder: (context, state) => _fadePage(state, const CompletionPage()),
    ),
  ],
);

CustomTransitionPage<void> _fadePage(GoRouterState state, Widget child) {
  return CustomTransitionPage<void>(
    key: state.pageKey,
    child: child,
    transitionsBuilder: (context, animation, secondaryAnimation, child) {
      return FadeTransition(
        opacity: CurveTween(curve: Curves.easeOutCubic).animate(animation),
        child: child,
      );
    },
  );
}
