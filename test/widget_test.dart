import 'package:flutter_test/flutter_test.dart';
import 'package:jlpt_ai_tutor/main.dart';

void main() {
  testWidgets('renders the app title', (tester) async {
    await tester.pumpWidget(const JlptAiTutorApp());

    expect(find.text('JLPT AI Tutor'), findsOneWidget);
  });
}
