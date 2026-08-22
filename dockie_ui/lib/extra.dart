// import 'dart:async';
// import 'dart:io';
// import 'dart:math';
// import 'package:flutter/material.dart';
// import 'package:flutter/services.dart';
// import 'package:window_manager/window_manager.dart';
// import 'package:sqlite3/sqlite3.dart' hide Row;

// // ── Path helpers ──
// String get _dbPath {
//   final profile = Platform.environment['USERPROFILE'] ??
//       Platform.environment['HOME'] ??
//       '.';
//   return '$profile\\.filefinder\\index.db';
// }

// // ── Entry point ──
// void main() async {
//   WidgetsFlutterBinding.ensureInitialized();
//   await windowManager.ensureInitialized();

//   const windowOptions = WindowOptions(
//     fullScreen: true,
//     backgroundColor: Colors.transparent,
//     skipTaskbar: true,
//     titleBarStyle: TitleBarStyle.hidden,
//   );

//   await windowManager.waitUntilReadyToShow(windowOptions, () async {
//     await windowManager.setAlwaysOnTop(true);
//     await windowManager.show();
//   });

//   runApp(const SpotlightApp());
// }

// // ── App root ──
// class SpotlightApp extends StatelessWidget {
//   const SpotlightApp({super.key});

//   @override
//   Widget build(BuildContext context) {
//     return MaterialApp(
//       title: 'FileFinder',
//       debugShowCheckedModeBanner: false,
//       home: const SearchOverlay(),
//     );
//   }
// }

// // ── Data ──
// class _SearchResult {
//   final String path;
//   final String filename;
//   final String fullText;
//   final int rank;
//   const _SearchResult(this.path, this.filename, this.fullText, this.rank);
// }

// // ── Overlay ──
// class SearchOverlay extends StatefulWidget {
//   const SearchOverlay({super.key});

//   @override
//   State<SearchOverlay> createState() => _SearchOverlayState();
// }

// class _SearchOverlayState extends State<SearchOverlay>
//     with SingleTickerProviderStateMixin {
//   final TextEditingController _searchController = TextEditingController();
//   final FocusNode _overlayFocusNode = FocusNode();
//   final ScrollController _scrollController = ScrollController();

//   late final FocusNode _textFocusNode;
//   late AnimationController _animationController;
//   late Animation<double> _fadeAnimation;

//   Database? _db;
//   bool _dbReady = false;
//   List<_SearchResult> _results = [];
//   int _selectedIndex = 0;
//   Timer? _debounce;
//   bool _showResults = false;

//   @override
//   void initState() {
//     super.initState();

//     _textFocusNode = FocusNode(onKeyEvent: _handleTextFieldKey);

//     _animationController = AnimationController(
//       duration: const Duration(milliseconds: 500),
//       vsync: this,
//     );
//     _fadeAnimation = CurvedAnimation(
//       parent: _animationController,
//       curve: Curves.easeIn,
//     );

//     _initDb();
//     _searchController.addListener(_onSearchChanged);

//     WidgetsBinding.instance.addPostFrameCallback((_) {
//       _textFocusNode.requestFocus();
//       _animationController.forward();
//     });
//   }

//   @override
//   void dispose() {
//     _debounce?.cancel();
//     _db?.dispose();
//     _animationController.dispose();
//     _searchController.dispose();
//     _overlayFocusNode.dispose();
//     _textFocusNode.dispose();
//     _scrollController.dispose();
//     super.dispose();
//   }

//   // ── DB ──
//   void _initDb() {
//     try {
//       if (!File(_dbPath).existsSync()) {
//         _dbReady = false;
//         return;
//       }
//       _db = sqlite3.open(_dbPath);
//       _dbReady = true;
//     } catch (_) {
//       _dbReady = false;
//     }
//   }

//   // ── Dismiss ──
//   void _dismiss() {
//     _searchController.clear();
//     _clearResults();
//   }

//   void _clearResults() {
//     setState(() {
//       _results = [];
//       _selectedIndex = 0;
//       _showResults = false;
//     });
//   }

//   // ── Search ──
//   void _onSearchChanged() {
//     final query = _searchController.text.trim();
//     if (query.isEmpty) {
//       _debounce?.cancel();
//       _clearResults();
//       return;
//     }
//     _debounce?.cancel();
//     _debounce = Timer(const Duration(milliseconds: 200), () {
//       _performSearch(query);
//     });
//   }

//   void _performSearch(String query) {
//     if (!_dbReady || _db == null) {
//       setState(() => _showResults = true);
//       return;
//     }

//     final likePrefix = '$query%';
//     final likeContains = '%$query%';

//     final rows;
//     try {
//       rows = _db!.select(
//         '''SELECT path, filename, COALESCE(text, '') AS text,
//                   CASE
//                       WHEN filename LIKE ? THEN 1
//                       WHEN filename LIKE ? THEN 2
//                       WHEN text IS NOT NULL AND text LIKE ? THEN 3
//                       ELSE 4
//                   END AS rank
//            FROM files
//            WHERE filename LIKE ?
//               OR (text IS NOT NULL AND text LIKE ?)
//            ORDER BY rank, filename
//            LIMIT 50''',
//         [likePrefix, likeContains, likeContains, likeContains, likeContains],
//       ).toList();
//     } catch (_) {
//       setState(() => _showResults = true);
//       return;
//     }

//     final results = <_SearchResult>[];
//     for (final row in rows) {
//       results.add(_SearchResult(
//         row['path'] as String,
//         row['filename'] as String,
//         row['text'] as String,
//         row['rank'] as int,
//       ));
//     }

//     setState(() {
//       _results = results;
//       _selectedIndex = results.isNotEmpty ? 0 : -1;
//       _showResults = true;
//     });

//     if (_scrollController.hasClients) {
//       _scrollController.jumpTo(0);
//     }
//   }

//   // ── File actions ──
//   void _openFile(String path) {
//     Process.run('cmd', ['/c', 'start', '', path]);
//   }

//   void _openFileLocation(String path) {
//     Process.run('explorer', ['/select,', path]);
//   }

//   void _activateSelected({bool location = false}) {
//     if (_results.isEmpty || _selectedIndex < 0) return;
//     final path = _results[_selectedIndex].path;
//     if (location) {
//       _openFileLocation(path);
//     } else {
//       _openFile(path);
//     }
//     _dismiss();
//   }

//   void _activateResult(int index) {
//     if (HardwareKeyboard.instance.isShiftPressed) {
//       _openFileLocation(_results[index].path);
//     } else {
//       _openFile(_results[index].path);
//     }
//     _dismiss();
//   }

//   // ── Keyboard ──
//   KeyEventResult _handleTextFieldKey(FocusNode node, KeyEvent evt) {
//     if (evt is! KeyDownEvent) return KeyEventResult.ignored;

//     if (evt.logicalKey == LogicalKeyboardKey.escape) {
//       _dismiss();
//       return KeyEventResult.handled;
//     }

//     if (evt.logicalKey == LogicalKeyboardKey.enter) {
//       if (_results.isEmpty) return KeyEventResult.ignored;
//       _activateSelected(
//         location: HardwareKeyboard.instance.isShiftPressed,
//       );
//       return KeyEventResult.handled;
//     }

//     if (evt.logicalKey == LogicalKeyboardKey.arrowDown) {
//       if (_results.isNotEmpty) {
//         setState(() {
//           _selectedIndex = min(_selectedIndex + 1, _results.length - 1);
//         });
//         _scrollToSelected();
//       }
//       return KeyEventResult.handled;
//     }

//     if (evt.logicalKey == LogicalKeyboardKey.arrowUp) {
//       if (_results.isNotEmpty) {
//         setState(() {
//           _selectedIndex = max(_selectedIndex - 1, 0);
//         });
//         _scrollToSelected();
//       }
//       return KeyEventResult.handled;
//     }

//     return KeyEventResult.ignored;
//   }

//   void _scrollToSelected() {
//     if (!_scrollController.hasClients) return;
//     const itemHeight = 78.0;
//     final offset = _selectedIndex * itemHeight;
//     final viewport = _scrollController.position.viewportDimension;
//     if (offset < _scrollController.offset) {
//       _scrollController.animateTo(offset,
//           duration: const Duration(milliseconds: 100), curve: Curves.easeOut);
//     } else if (offset + itemHeight > _scrollController.offset + viewport) {
//       _scrollController.animateTo(offset + itemHeight - viewport,
//           duration: const Duration(milliseconds: 100), curve: Curves.easeOut);
//     }
//   }

//   void _onOverlayKey(KeyEvent event) {
//     if (event is KeyDownEvent &&
//         event.logicalKey == LogicalKeyboardKey.escape) {
//       _dismiss();
//     }
//   }

//   // ── Snippet ──
//   String _makeSnippet(String text, String query) {
//     if (text.isEmpty || query.isEmpty) return '';
//     final idx = text.toLowerCase().indexOf(query.toLowerCase());
//     if (idx == -1) return '';
//     const context = 80;
//     final start = max(0, idx - context ~/ 2);
//     final end = min(text.length, idx + query.length + context ~/ 2);
//     var snip = text.substring(start, end).replaceAll('\n', ' ').trim();
//     if (start > 0) snip = '\u2026$snip';
//     if (end < text.length) snip = '$snip\u2026';
//     return snip;
//   }

//   // ── Highlight helper ──
//   List<TextSpan> _highlightText(String text, String query) {
//     if (query.isEmpty) return [TextSpan(text: text)];
//     final lower = text.toLowerCase();
//     final q = query.toLowerCase();
//     final spans = <TextSpan>[];
//     int start = 0;
//     while (true) {
//       final idx = lower.indexOf(q, start);
//       if (idx == -1) {
//         if (start < text.length) {
//           spans.add(TextSpan(text: text.substring(start)));
//         }
//         break;
//       }
//       if (idx > start) {
//         spans.add(TextSpan(text: text.substring(start, idx)));
//       }
//       spans.add(TextSpan(
//         text: text.substring(idx, idx + query.length),
//         style: const TextStyle(
//           backgroundColor: Color(0xFFFFEB3B),
//           fontWeight: FontWeight.w600,
//           color: Colors.black,
//         ),
//       ));
//       start = idx + query.length;
//     }
//     return spans;
//   }

//   // ── Build ──
//   @override
//   Widget build(BuildContext context) {
//     final screenWidth = MediaQuery.sizeOf(context).width;
//     final panelWidth = screenWidth * 0.35;

//     return Scaffold(
//       backgroundColor: Colors.transparent,
//       body: FadeTransition(
//         opacity: _fadeAnimation,
//         child: KeyboardListener(
//           focusNode: _overlayFocusNode,
//           onKeyEvent: _onOverlayKey,
//           child: GestureDetector(
//             onTap: _dismiss,
//             child: Container(
//               color: Colors.black.withOpacity(0.15),
//               child: Column(
//                 mainAxisSize: MainAxisSize.min,
//                 children: [
//                   _buildSearchBar(),
//                   if (_showResults) _buildResultsPanel(),
//                 ],
//               ),
//             ),
//           ),
//         ),
//       ),
//     );
//   }

//   // ── Search bar ──
//   Widget _buildSearchBar() {
//     return Center(
//       child: GestureDetector(
//         onTap: () {}, // prevent backdrop dismiss
//         child: Container(
//           width: MediaQuery.sizeOf(context).width * 0.35,
//           height: 50,
//           padding: const EdgeInsets.only(
//               left: 10, right: 10, top: 10, bottom: 5),
//           decoration: BoxDecoration(
//             color: Colors.white.withOpacity(0.95),
//             borderRadius: _showResults
//                 ? const BorderRadius.vertical(top: Radius.circular(7))
//                 : BorderRadius.circular(7),
//             boxShadow: const [
//               BoxShadow(
//                 color: Colors.black,
//                 blurRadius: 10,
//                 offset: Offset(0, 0),
//               ),
//             ],
//           ),
//           child: TextField(
//             controller: _searchController,
//             focusNode: _textFocusNode,
//             textAlignVertical: TextAlignVertical.center,
//             autofocus: true,
//             decoration: InputDecoration(
//               hintText: 'What file are you looking for?',
//               helperText:
//               '\u2191\u2193 navigate  \u23CE open  Shift+\u23CE location  Esc dismiss',
//               helperStyle: const TextStyle(fontSize: 9),
//               border: InputBorder.none,
//               isDense: true,
//               isCollapsed: true,
//               icon:
//               const Icon(Icons.search, size: 20, color: Colors.black),
//             ),
//           ),
//         ),
//       ),
//     );
//   }

//   // ── Results panel ──
//   Widget _buildResultsPanel() {
//     if (!_dbReady) {
//       return _buildStatusTile(
//           const Icon(Icons.hourglass_empty, size: 18, color: Colors.grey),
//           'Index not ready',
//           'The PDF index is still being built. Please wait.');
//     }

//     if (_results.isEmpty) {
//       return _buildStatusTile(
//           const Icon(Icons.search_off, size: 18, color: Colors.grey),
//           'No files found',
//           'Try a different search term.');
//     }

//     const maxHeight = 400.0;
//     final totalHeight = _results.length * 78.0;
//     final height = min(totalHeight, maxHeight);

//     return Container(
//       height: height,
//       decoration: const BoxDecoration(
//         color: Color(0xFFF8F8F8),
//         borderRadius:
//         BorderRadius.vertical(bottom: Radius.circular(7)),
//         boxShadow: [
//           BoxShadow(
//             color: Colors.black26,
//             blurRadius: 10,
//             offset: Offset(0, 4),
//           ),
//         ],
//       ),
//       child: ClipRRect(
//         borderRadius:
//         const BorderRadius.vertical(bottom: Radius.circular(7)),
//         child: ListView.builder(
//           controller: _scrollController,
//           itemCount: _results.length,
//           itemExtent: 78,
//           itemBuilder: (context, index) {
//             return _buildResultTile(index);
//           },
//         ),
//       ),
//     );
//   }

//   Widget _buildStatusTile(Icon icon, String title, String subtitle) {
//     return Container(
//       width: double.infinity,
//       decoration: const BoxDecoration(
//         color: Color(0xFFF8F8F8),
//         borderRadius:
//         BorderRadius.vertical(bottom: Radius.circular(7)),
//         boxShadow: [
//           BoxShadow(
//             color: Colors.black26,
//             blurRadius: 10,
//             offset: Offset(0, 4),
//           ),
//         ],
//       ),
//       child: Padding(
//         padding: const EdgeInsets.symmetric(vertical: 28, horizontal: 16),
//         child: Column(
//           children: [
//             icon,
//             const SizedBox(height: 8),
//             Text(title,
//                 style: const TextStyle(
//                     fontSize: 13, color: Colors.black54)),
//             const SizedBox(height: 4),
//             Text(subtitle,
//                 style: const TextStyle(
//                     fontSize: 11, color: Colors.black38)),
//           ],
//         ),
//       ),
//     );
//   }

//   Widget _buildResultTile(int index) {
//     final result = _results[index];
//     final isSelected = index == _selectedIndex;
//     final query = _searchController.text.trim();

//     final snippet = result.rank <= 3
//         ? _makeSnippet(result.fullText, query)
//         : '';

//     return GestureDetector(
//       onTap: () => _activateResult(index),
//       child: Container(
//         color: isSelected
//             ? Colors.blue.withOpacity(0.10)
//             : Colors.transparent,
//         padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
//         child: Column(
//           crossAxisAlignment: CrossAxisAlignment.start,
//           mainAxisAlignment: MainAxisAlignment.center,
//           children: [
//             // Filename
//             RichText(
//               text: TextSpan(
//                 style: const TextStyle(
//                     fontSize: 13,
//                     color: Colors.black87,
//                     fontWeight: FontWeight.w600),
//                 children: _highlightText(result.filename, query),
//               ),
//               maxLines: 1,
//               overflow: TextOverflow.ellipsis,
//             ),
//             if (snippet.isNotEmpty) ...[
//               const SizedBox(height: 3),
//               RichText(
//                 text: TextSpan(
//                   style: const TextStyle(
//                       fontSize: 12, color: Colors.black54),
//                   children: _highlightText(snippet, query),
//                 ),
//                 maxLines: 1,
//                 overflow: TextOverflow.ellipsis,
//               ),
//             ],
//             const SizedBox(height: 3),
//             // File path
//             Text(
//               result.path,
//               style: const TextStyle(
//                   fontSize: 10, color: Colors.black38),
//               maxLines: 1,
//               overflow: TextOverflow.ellipsis,
//             ),
//           ],
//         ),
//       ),
//     );
//   }
// }
