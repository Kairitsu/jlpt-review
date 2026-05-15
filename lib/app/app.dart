import 'package:flutter/material.dart';

import 'router.dart';
import 'theme.dart';

class JlptAiTutorApp extends StatelessWidget {
  const JlptAiTutorApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'JLPT AI Tutor',
      debugShowCheckedModeBanner: false,
      theme: buildAppTheme(),
      initialRoute: AppRoute.home,
      onGenerateRoute: onGenerateAppRoute,
    );
  }
}
