// This is a basic Flutter widget test.
//
// To perform an interaction with a widget in your test, use the WidgetTester
// utility in the flutter_test package. For example, you can send tap and scroll
// gestures. You can also use WidgetTester to find child widgets in the widget
// tree, read text, and verify that the values of widget properties are correct.

import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import 'package:dockie_ui/main.dart';

void main() {
  testWidgets('Dockie overlay shows status when index missing', (tester) async {
    // Point the UI at a non-existent DB so the test stays hermetic (never
    // opens the real index).
    dbPathOverride = '${Directory.systemTemp.path}\\dockie-test\\index.db';

    await tester.pumpWidget(const SpotlightApp());
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.text('Index not ready'), findsOneWidget);

    dbPathOverride = '';
  });
}
