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
  testWidgets('Overlay renders search bar and inline status when index missing',
      (tester) async {
    // Point the UI at a non-existent DB so the test stays hermetic (never
    // opens the real index).
    dbPathOverride = '${Directory.systemTemp.path}\\dockie-test\\index.db';

    await tester.pumpWidget(SpotlightApp());
    await tester.pump(const Duration(milliseconds: 100));

    // The spotlight search bar must still render (regression: the whole
    // screen used to turn opaque white) with the status inline below it.
    expect(find.text('What file are you looking for?'), findsOneWidget);
    expect(find.text('Index not ready'), findsOneWidget);

    dbPathOverride = '';
  });
}
