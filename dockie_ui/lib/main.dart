import 'dart:async';
import 'dart:convert';
import 'dart:ffi';
import 'dart:io';
import 'dart:math';

import 'package:ffi/ffi.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:hotkey_manager/hotkey_manager.dart';
import 'package:sqlite3/sqlite3.dart' hide Row;
import 'package:tray_manager/tray_manager.dart';
import 'package:window_manager/window_manager.dart';

// The overlay widget is driven from main() through this key.
final GlobalKey<SearchOverlayState> _overlayKey =
    GlobalKey<SearchOverlayState>();

// Set by main() so the overlay widget can ask the window layer to hide it
// without terminating the process.
void Function()? onOverlayDismissRequested;

// ---------------------------------------------------------------------------
// Main entry. The Flutter app is the Dockie main process: it owns the
// triple-Ctrl hotkey, the tray icon and the search overlay, and keeps the
// Python backend (Dockie.exe, same folder) alive as a child process.
// ---------------------------------------------------------------------------
void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // One instance only. The named mutex also doubles as the installer's
  // AppMutex=Dockie, so an update can close a running app before replacing
  // its files (see dockie_installer.iss).
  if (!_tryAcquireAppMutex()) {
    return;
  }

  _log('Dockie UI (main process) starting, dbPath=$_dbPath');
  try {
    await windowManager.ensureInitialized();
  } catch (e) {
    _log('windowManager.ensureInitialized failed: $e', level: 'ERROR');
  }

  // Configure the window as a borderless, transparent, full-screen overlay
  // that is invisible and click-through until summoned (see _setOverlayInert).
  const windowOptions = WindowOptions(
    fullScreen: true,
    backgroundColor: Colors.transparent,
    skipTaskbar: true,
    titleBarStyle: TitleBarStyle.hidden,
  );

  try {
    windowManager.waitUntilReadyToShow(windowOptions, () async {
      await windowManager.setAlwaysOnTop(true);
      await windowManager.setSkipTaskbar(true);
      // Alt+F4 (or any close request) dismisses the overlay instead of
      // terminating the process.
      await windowManager.setPreventClose(true);
      await _setOverlayInert(true);
      _log('Window ready (inert overlay)');
    });
  } catch (e) {
    _log('waitUntilReadyToShow failed: $e', level: 'ERROR');
  }

  windowManager.addListener(_DockieWindowListener());

  // Backend child process (Dockie.exe), hotkey and tray start alongside.
  _backend = BackendManager(backendStatus);
  _backend!.start();
  _registerTripleCtrlHotkey();
  _initTray();

  onOverlayDismissRequested = _dismissOverlay;
  runApp(SpotlightApp(overlayKey: _overlayKey));
}

class _DockieWindowListener with WindowListener {
  @override
  void onWindowClose() {
    _log('Window close requested - dismissing overlay');
    _dismissOverlay();
  }
}

// ---------------------------------------------------------------------------
// Logging. The UI writes to the same dockie.log the backend appends to
// (~/.dockie/dockie.log); lines carry a [flutter] tag to tell them apart.
// ---------------------------------------------------------------------------
String _two(int n) => n.toString().padLeft(2, '0');

IOSink? _logSink;

void _ensureLogSink() {
  if (_logSink != null) return;
  try {
    final profile = Platform.environment['USERPROFILE'] ??
        Platform.environment['HOME'] ??
        '.';
    final dir = Directory('$profile\\.dockie');
    dir.createSync(recursive: true);
    _logSink = File('${dir.path}\\dockie.log').openWrite(mode: FileMode.append);
  } catch (_) {
    _logSink = null; // fall back to stdout below
  }
}

void _log(String message, {String level = 'INFO'}) {
  final t = DateTime.now();
  final line = '[${t.year}-${_two(t.month)}-${_two(t.day)} '
      '${_two(t.hour)}:${_two(t.minute)}:${_two(t.second)}] '
      '[$level] [flutter] $message';
  _ensureLogSink();
  if (_logSink != null) {
    try {
      _logSink!.writeln(line);
    } catch (_) {
      // ignore: dropped log line
    }
  } else {
    // ignore: avoid_print - fallback when the log file is unavailable.
    print(line);
  }
}

// ---------------------------------------------------------------------------
// FFI helpers: single-instance mutex, foreground-lock grant, key state.
// ---------------------------------------------------------------------------
final DynamicLibrary _kernel32 = DynamicLibrary.open('kernel32.dll');
final DynamicLibrary _user32 = DynamicLibrary.open('user32.dll');

typedef _CreateMutexWFn =
    Pointer<Void> Function(Pointer<Void>, Int32, Pointer<Utf16>);
typedef _CreateMutexWDart =
    Pointer<Void> Function(Pointer<Void>, int, Pointer<Utf16>);

typedef _GetLastErrorFn = Uint32 Function();
typedef _GetLastErrorDart = int Function();

typedef _CloseHandleFn = Int32 Function(Pointer<Void>);
typedef _CloseHandleDart = int Function(Pointer<Void>);

typedef _KeybdEventFn = Void Function(Uint8, Uint8, Uint32, UintPtr);
typedef _KeybdEventDart = void Function(int, int, int, int);

typedef _GetAsyncKeyStateFn = Int16 Function(Int32);
typedef _GetAsyncKeyStateDart = int Function(int);

final _createMutexW =
    _kernel32.lookupFunction<_CreateMutexWFn, _CreateMutexWDart>('CreateMutexW');
final _getLastError = _kernel32.lookupFunction<_GetLastErrorFn, _GetLastErrorDart>(
    'GetLastError');
final _closeHandle = _kernel32.lookupFunction<_CloseHandleFn, _CloseHandleDart>(
    'CloseHandle');
final _keybdEvent = _user32.lookupFunction<_KeybdEventFn, _KeybdEventDart>(
    'keybd_event');
final _getAsyncKeyState = _user32.lookupFunction<_GetAsyncKeyStateFn,
    _GetAsyncKeyStateDart>('GetAsyncKeyState');

Pointer<Void>? _appMutexHandle;

/// Creates the 'Dockie' named mutex. Returns false when another instance is
/// already running (the handle is intentionally kept for the process
/// lifetime so the installer's AppMutex=Dockie finds it).
bool _tryAcquireAppMutex() {
  try {
    final name = 'Dockie'.toNativeUtf16();
    _appMutexHandle = _createMutexW(Pointer.fromAddress(0), 0, name);
    malloc.free(name);
    if (_getLastError() == 183 /* ERROR_ALREADY_EXISTS */) {
      _log('Another Dockie instance is running - exiting');
      return false;
    }
    return true;
  } catch (e) {
    _log('App mutex check failed: $e', level: 'WARN');
    return true;
  }
}

/// Grants this process the Windows foreground lock by injecting a benign
/// F24 key-up. Injected input marks the process as the last-input recipient,
/// which is what allows SetForegroundWindow to succeed when summoning the
/// overlay from a global hotkey.
void _grantForegroundLock() {
  try {
    _keybdEvent(0x87 /* VK_F24 */, 0, 0x0002 /* KEYEVENTF_KEYUP */, 0);
  } catch (e) {
    _log('Foreground lock injection failed: $e', level: 'WARN');
  }
}

bool _isCtrlDown() => (_getAsyncKeyState(0x11 /* VK_CONTROL */) & 0x8000) != 0;

// ---------------------------------------------------------------------------
// Backend child process (Dockie.exe) + line-based IPC over stdin/stdout.
//   Flutter -> backend (stdin): PING | SHUTDOWN | GET_STATUS | GET_VERSION |
//                               GET_RUN_ON_STARTUP | RUN_ON_STARTUP <0|1>
//   backend -> Flutter (stdout): READY | VERSION <v> | STATUS <phase> <found>
//                               <done> <current> | RUN_ON_STARTUP <0|1> |
//                               PONG | UPDATE_EXITING <v>
// ---------------------------------------------------------------------------
class BackendStatus extends ChangeNotifier {
  String phase = 'stopped'; // stopped | starting | idle | scan | extract | done
  int found = 0;
  int done = 0;
  String current = '';
  String version = '';
  bool runOnStartup = true;
  bool updating = false;

  /// Public wrapper around the protected [notifyListeners] so the
  /// BackendManager can broadcast changes.
  void notify() => notifyListeners();
}

final BackendStatus backendStatus = BackendStatus();

class BackendManager {
  BackendManager(this.status);

  final BackendStatus status;
  Process? _proc;
  bool _intentionalStop = false;
  int _restartAttempts = 0;
  Timer? _restartTimer;
  final List<String> _pendingCommands = [];

  String? _findBackendExe() {
    // Test/dev override; packaged builds always find Dockie.exe next to the
    // Flutter executable.
    final override = Platform.environment['DOCKIE_BACKEND_EXE'];
    if (override != null && override.isNotEmpty && File(override).existsSync()) {
      return override;
    }
    final exeDir = File(Platform.resolvedExecutable).parent.path;
    final candidates = <String>[
      '$exeDir\\Dockie.exe',
      '${Directory.current.path}\\Dockie.exe',
      // Dev layout: repo/dist/Dockie.exe relative to the dockie_ui project.
      '${Directory.current.path}\\..\\dist\\Dockie.exe',
    ];
    for (final candidate in candidates) {
      if (File(candidate).existsSync()) return candidate;
    }
    return null;
  }

  Future<void> start() async {
    if (_intentionalStop) return;
    final exe = _findBackendExe();
    if (exe == null) {
      _log('Backend Dockie.exe not found - indexing will not run',
          level: 'ERROR');
      return;
    }
    try {
      final env = Map<String, String>.from(Platform.environment);
      env['DOCKIE_PARENT_PID'] = '$pid';
      _log('Launching backend: $exe');
      status.phase = 'starting';
      status.notify();
      _proc = await Process.start(
        exe,
        const [],
        environment: env,
        workingDirectory: File(exe).parent.path,
      );
      _proc!.stdout
          .transform(utf8.decoder)
          .transform(const LineSplitter())
          .listen(_onBackendLine);
      _proc!.stderr
          .transform(utf8.decoder)
          .transform(const LineSplitter())
          .listen((line) => _log('backend(stderr): $line', level: 'WARN'));
      _proc!.exitCode.then((code) {
        _log('Backend exited (code=$code)');
        _onBackendExited();
      });
      final pending = List.of(_pendingCommands);
      _pendingCommands.clear();
      for (final command in pending) {
        _send(command);
      }
    } catch (e) {
      _log('Failed to launch backend: $e', level: 'ERROR');
    }
  }

  void _onBackendExited() {
    _proc = null;
    if (_intentionalStop || status.updating) return;
    // Keep the backend alive: the overlay depends on its index. Back off
    // after repeated crashes to avoid a relaunch loop.
    _restartAttempts++;
    final delay = _restartAttempts <= 3 ? 2 : 15;
    _restartTimer?.cancel();
    _restartTimer = Timer(Duration(seconds: delay), () {
      status.phase = 'starting';
      status.notify();
      start();
    });
  }

  void _onBackendLine(String line) {
    line = line.trim();
    if (line.isEmpty) return;
    if (line.startsWith('STATUS ')) {
      final parts = line.split(' ');
      status.phase = parts.length > 1 ? parts[1] : status.phase;
      status.found =
          parts.length > 2 ? int.tryParse(parts[2]) ?? status.found : status.found;
      status.done =
          parts.length > 3 ? int.tryParse(parts[3]) ?? status.done : status.done;
      status.current = parts.length > 4 ? parts.sublist(4).join(' ') : '';
      status.notify();
    } else if (line.startsWith('VERSION ')) {
      status.version = line.substring('VERSION '.length).trim();
      _updateTrayMenu();
    } else if (line.startsWith('RUN_ON_STARTUP ')) {
      final on = line.substring('RUN_ON_STARTUP '.length).trim() == '1';
      status.runOnStartup = on;
      _runOnStartup = on;
      _updateTrayMenu();
    } else if (line == 'READY') {
      status.phase = 'idle';
      status.notify();
    } else if (line.startsWith('UPDATE_EXITING')) {
      // Backend found a newer release and is handing off to the installer.
      // Exit ourselves so no file lock or AppMutex is held when files are
      // replaced; the installer relaunches the new dockie_ui.exe.
      status.updating = true;
      status.notify();
      _log('Backend exiting for update: $line');
      _exitApp();
    } else {
      _log('backend: $line');
    }
  }

  void send(String command) {
    if (_proc == null) {
      _pendingCommands.add(command);
      return;
    }
    _send(command);
  }

  void _send(String command) {
    try {
      _proc?.stdin.writeln(command);
    } catch (e) {
      _log('Backend send failed: $e', level: 'WARN');
    }
  }

  Future<void> stop() async {
    _intentionalStop = true;
    if (_proc == null) return;
    _send('SHUTDOWN');
    try {
      final code = await _proc!.exitCode.timeout(const Duration(seconds: 4),
          onTimeout: () {
        _log('Backend did not exit in time - killing');
        _proc!.kill();
        return -1;
      });
      _log('Backend stopped (code=$code)');
    } catch (e) {
      _log('Backend stop failed: $e', level: 'WARN');
    }
    _proc = null;
  }
}

BackendManager? _backend;

// ---------------------------------------------------------------------------
// Triple-Ctrl hotkey (hotkey_manager). RegisterHotKey with VK_CONTROL fires
// a WM_HOTKEY on every Ctrl press (left or right); three presses within one
// second summon the overlay.
// ---------------------------------------------------------------------------
const Duration _kTripleCtrlWindow = Duration(seconds: 1);
HotKey? _tripleCtrlHotKey;
final List<DateTime> _ctrlPresses = [];
Timer? _ctrlWindowTimer;

// Auto-repeat protection: WM_HOTKEY repeats while a key is held, so only
// count a press once the previous one has been released (observed via
// GetAsyncKeyState polling between presses).
bool _ctrlHeld = false;
Timer? _ctrlReleaseWatch;

Future<void> _registerTripleCtrlHotkey() async {
  try {
    await hotKeyManager.unregisterAll();
    _tripleCtrlHotKey = HotKey(
      // Maps to Windows VK_CONTROL (0x11); the modifier plus the control key
      // collapses to "Ctrl pressed", left or right.
      key: PhysicalKeyboardKey.controlLeft,
      modifiers: [HotKeyModifier.control],
      scope: HotKeyScope.system,
    );
    await hotKeyManager.register(
      _tripleCtrlHotKey!,
      keyDownHandler: (hotKey) => _onCtrlPressed(),
    );
    _log('Triple-Ctrl hotkey registered (hotkey_manager)');
  } catch (e) {
    _log('Hotkey registration failed: $e', level: 'ERROR');
  }
}

void _onCtrlPressed() {
  if (_ctrlHeld) return; // keyboard auto-repeat while held - ignore
  _ctrlHeld = true;
  _ctrlReleaseWatch?.cancel();
  _ctrlReleaseWatch = Timer.periodic(const Duration(milliseconds: 30), (_) {
    if (!_isCtrlDown()) {
      _ctrlHeld = false;
      _ctrlReleaseWatch?.cancel();
    }
  });

  final now = DateTime.now();
  _ctrlPresses.add(now);
  _ctrlPresses.removeWhere((t) => now.difference(t) > _kTripleCtrlWindow);
  _ctrlWindowTimer?.cancel();
  _ctrlWindowTimer = Timer(_kTripleCtrlWindow, () => _ctrlPresses.clear());

  if (_ctrlPresses.length >= 3) {
    _ctrlPresses.clear();
    _ctrlWindowTimer?.cancel();
    _log('Hotkey: triple-Ctrl -> summon overlay');
    _summonOverlay();
  }
}

// ---------------------------------------------------------------------------
// System tray (tray_manager).
// ---------------------------------------------------------------------------
bool _runOnStartup = true;

class _DockieTrayListener extends TrayListener {
  @override
  void onTrayIconMouseDown() {
    _summonOverlay();
  }

  @override
  void onTrayIconRightMouseDown() {
    trayManager.popUpContextMenu();
  }

  @override
  void onTrayMenuItemClick(MenuItem menuItem) {
    switch (menuItem.key) {
      case 'show':
        _summonOverlay();
        break;
      case 'run-on-startup':
        _toggleRunOnStartup();
        break;
      case 'exit':
        _exitApp();
        break;
    }
  }
}

_DockieTrayListener? _trayListener;

Future<void> _initTray() async {
  try {
    _trayListener = _DockieTrayListener();
    trayManager.addListener(_trayListener!);
    // tray_manager resolves the path relative to <exe>\data\flutter_assets.
    await trayManager.setIcon('assets/robot.ico');
    await trayManager.setToolTip('Dockie');
    await _updateTrayMenu();
    _log('Tray initialized');
  } catch (e) {
    _log('Tray init failed: $e', level: 'ERROR');
  }
}

Future<void> _updateTrayMenu() async {
  final version = backendStatus.version;
  try {
    await trayManager.setContextMenu(Menu(items: [
      MenuItem(key: 'show', label: 'Show'),
      MenuItem.separator(),
      MenuItem.checkbox(
        key: 'run-on-startup',
        label: 'Run on startup',
        checked: _runOnStartup,
      ),
      MenuItem.separator(),
      MenuItem(
        key: 'version',
        label: version.isEmpty ? 'Version ...' : 'Version $version',
        disabled: true,
      ),
      MenuItem(key: 'exit', label: 'Exit'),
    ]));
  } catch (e) {
    _log('Tray menu update failed: $e', level: 'WARN');
  }
}

void _toggleRunOnStartup() {
  _runOnStartup = !_runOnStartup;
  _updateTrayMenu();
  _backend?.send('RUN_ON_STARTUP ${_runOnStartup ? 1 : 0}');
}

// ---------------------------------------------------------------------------
// Window control: the overlay is always visible so the Flutter engine keeps
// a valid view size (hiding it leaves a stale size and the overlay later
// renders off-center or not at all). When idle it is inert - fully
// transparent, click-through and unfocused; triple-Ctrl flips it live.
// ---------------------------------------------------------------------------
Future<void> _setOverlayInert(bool inert) async {
  try {
    await windowManager.setOpacity(inert ? 0 : 1);
    await windowManager.setIgnoreMouseEvents(inert);
    if (inert) {
      await windowManager.blur();
    }
  } catch (e) {
    _log('setOverlayInert($inert) failed: $e', level: 'ERROR');
  }
}

Future<void> _summonOverlay() async {
  try {
    await windowManager.setOpacity(1);
    await windowManager.setIgnoreMouseEvents(false);
    await windowManager.show();
    _grantForegroundLock();
    await windowManager.focus();
    _overlayKey.currentState?.summon();
  } catch (e) {
    _log('Summon overlay failed: $e', level: 'ERROR');
  }
}

Future<void> _dismissOverlay() async {
  _overlayKey.currentState?.dismiss();
  await _setOverlayInert(true);
}

// ---------------------------------------------------------------------------
// App exit: stop the backend, unregister the hotkey, destroy the tray and
// the window, then leave.
// ---------------------------------------------------------------------------
Future<void> _exitApp() async {
  _log('Exiting app');
  _ctrlWindowTimer?.cancel();
  _ctrlReleaseWatch?.cancel();
  if (_appMutexHandle != null) {
    _closeHandle(_appMutexHandle!);
    _appMutexHandle = null;
  }
  try {
    await hotKeyManager.unregisterAll();
  } catch (e) {
    _log('Hotkey unregister failed: $e', level: 'WARN');
  }
  try {
    await trayManager.destroy();
  } catch (e) {
    _log('Tray destroy failed: $e', level: 'WARN');
  }
  await _backend?.stop();
  try {
    await windowManager.destroy();
  } catch (e) {
    _log('Window destroy failed: $e', level: 'WARN');
  }
  exit(0);
}

// ---------------------------------------------------------------------------
// Path helpers.
// ---------------------------------------------------------------------------
// Test hook: overridden by widget_test.dart so the UI never touches the real
// DB (Platform.environment is unmodifiable in the test harness).
String dbPathOverride = '';

// The backend keeps the index at ~/.dockie/index.db (see db._data_dir()).
// DOCKIE_DB_PATH and the exe-adjacent fallback remain for dev runs and
// legacy installs.
String get _dbPath {
  if (dbPathOverride.isNotEmpty) return dbPathOverride;
  final fromEnv = Platform.environment['DOCKIE_DB_PATH'];
  if (fromEnv != null && fromEnv.isNotEmpty) {
    return fromEnv;
  }
  final profile = Platform.environment['USERPROFILE'] ??
      Platform.environment['HOME'] ??
      '.';
  final userDb = '$profile\\.dockie\\index.db';
  if (File(userDb).existsSync()) {
    return userDb;
  }
  final exeDir = File(Platform.resolvedExecutable).parent.path;
  if (File('$exeDir\\index.db').existsSync()) {
    return '$exeDir\\index.db';
  }
  return userDb;
}

class _SearchResult {
  final String path;
  final String filename;
  final String fullText;
  final int rank;
  const _SearchResult(this.path, this.filename, this.fullText, this.rank);
}

class SpotlightApp extends StatelessWidget {
  const SpotlightApp({super.key, this.overlayKey});

  final GlobalKey<SearchOverlayState>? overlayKey;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Dockie Spotlight Search',
      debugShowCheckedModeBanner: false,
      home: SearchOverlay(key: overlayKey),
    );
  }
}

class SearchOverlay extends StatefulWidget {
  const SearchOverlay({super.key});

  @override
  State<SearchOverlay> createState() => SearchOverlayState();
}

class SearchOverlayState extends State<SearchOverlay>
    with SingleTickerProviderStateMixin {
  final TextEditingController _searchController = TextEditingController();
  final FocusNode _overlayFocusNode = FocusNode();
  late final FocusNode _textFocusNode = FocusNode(
    onKeyEvent: _handleTextFieldKey,
  );
  late AnimationController _animationController;
  late Animation<double> _fadeAnimation;

  Database? _db;
  bool _dbReady = false;
  List<_SearchResult> _results = [];
  int _selectedIndex = 0;
  Timer? _debounce;
  final ScrollController _scrollController = ScrollController();
  // Per-row keys so keyboard navigation can scroll the selected row into view
  // even though rows now have intrinsic (variable) heights.
  final Map<int, GlobalKey> _itemKeys = {};

  @override
  void initState() {
    super.initState();
    _log('UI state initializing');
    // Initialize the animation controller for a fade-in effect on summon.
    _animationController = AnimationController(
      duration: const Duration(milliseconds: 500),
      vsync: this,
    );
    _fadeAnimation = CurvedAnimation(
      parent: _animationController,
      curve: Curves.easeIn,
    );

    _initDb();
    _searchController.addListener(_onSearchChanged);
    backendStatus.addListener(_onBackendStatusChanged);
  }

  @override
  void dispose() {
    backendStatus.removeListener(_onBackendStatusChanged);
    _animationController.dispose();
    _searchController.dispose();
    _overlayFocusNode.dispose();
    _textFocusNode.dispose();
    _scrollController.dispose();
    _debounce?.cancel();
    _db?.dispose();
    super.dispose();
  }

  void _onBackendStatusChanged() {
    if (!mounted) return;
    setState(() {});
  }

  /// Called by main() when the overlay should become visible.
  void summon() {
    _log('Overlay summoned');
    _searchController.clear();
    _clearResults();
    _animationController.forward(from: 0);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _textFocusNode.requestFocus();
    });
  }

  /// Called by main() when the overlay should go back to inert.
  void dismiss() {
    _log('Overlay dismissed');
    _searchController.clear();
    _clearResults();
    _overlayFocusNode.unfocus();
  }

  // Hide the overlay. The main process keeps running (hotkey + tray + backend).
  void _hideOverlay() {
    _log('Hiding overlay');
    onOverlayDismissRequested?.call();
  }

  // Highlight helper
  List<TextSpan> _highlightText(String text, String query) {
    if (query.isEmpty) return [TextSpan(text: text)];
    final lower = text.toLowerCase();
    final q = query.toLowerCase();
    final spans = <TextSpan>[];
    int start = 0;
    while (true) {
      final idx = lower.indexOf(q, start);
      if (idx == -1) {
        if (start < text.length) {
          spans.add(TextSpan(text: text.substring(start)));
        }
        break;
      }
      if (idx > start) {
        spans.add(TextSpan(text: text.substring(start, idx)));
      }
      spans.add(TextSpan(
        text: text.substring(idx, idx + query.length),
        style: const TextStyle(
          backgroundColor: Color(0xFFFFEB3B),
          fontWeight: FontWeight.w600,
          color: Colors.black,
        ),
      ));
      start = idx + query.length;
    }
    return spans;
  }

  // DB
  void _initDb() {
    final path = _dbPath;
    _log('DB init: path=$path');
    try {
      if (!File(path).existsSync()) {
        _dbReady = false;
        _log('DB init: index.db NOT FOUND at $path', level: 'WARN');
        return;
      }
      _db = sqlite3.open(path);
      _dbReady = true;
      _log('DB init: opened $path');
    } catch (e) {
      _dbReady = false;
      _log('DB init: failed to open $path: $e', level: 'ERROR');
    }
  }

  void _clearResults() {
    setState(() {
      _results = [];
      _selectedIndex = 0;
      _itemKeys.clear();
    });
  }

  // Search
  void _onSearchChanged() {
    final query = _searchController.text.trim();
    if (query.isEmpty) {
      _debounce?.cancel();
      _clearResults();
      return;
    }
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 200), () {
      _performSearch(query);
    });
  }

  void _performSearch(String query) {
    if (!_dbReady) {
      _log('Search: DB not ready for query "$query"', level: 'WARN');
      return;
    }

    final likePrefix = '$query%';
    final likeContains = '%$query%';

    final results = <_SearchResult>[];
    final stopwatch = Stopwatch()..start();
    // Use a fresh connection per search so we always read the latest committed
    // rows (the watcher/worker write to the DB from another process).
    final db = sqlite3.open(_dbPath);
    try {
      final rows = db.select(
        '''SELECT path, filename, COALESCE(text, '') AS text,
                  CASE
                      WHEN filename LIKE ? THEN 1
                      WHEN filename LIKE ? THEN 2
                      WHEN text IS NOT NULL AND text LIKE ? THEN 3
                      ELSE 4
                  END AS rank
           FROM files
           WHERE filename LIKE ?
              OR (text IS NOT NULL AND text LIKE ?)
           ORDER BY rank, filename
           LIMIT 50''',
        [likePrefix, likeContains, likeContains, likeContains, likeContains],
      );

      for (final row in rows) {
        results.add(_SearchResult(
          row['path'] as String,
          row['filename'] as String,
          row['text'] as String,
          row['rank'] as int,
        ));
      }
    } catch (e) {
      // Leave results empty rather than showing stale rows.
      _log('Search: query failed "$query": $e', level: 'ERROR');
    } finally {
      db.dispose();
    }
    stopwatch.stop();
    _log('Search: "$query" -> ${results.length} results in '
        '${stopwatch.elapsedMilliseconds} ms');

    setState(() {
      _results = results;
      _selectedIndex = results.isNotEmpty ? 0 : -1;
      _itemKeys.clear();
    });

    if (_scrollController.hasClients) {
      _scrollController.jumpTo(0);
    }
  }

  // File actions
  Future<void> _openFile(String path) async {
    _log('Open file: $path');
    try {
      await Process.run('cmd', ['/c', 'start', '', path]);
    } catch (e) {
      _log('Open file failed: $path: $e', level: 'ERROR');
    }
  }

  Future<void> _openFileLocation(String path) async {
    _log('Reveal in Explorer: $path');
    try {
      // Detached so the launched process survives the app exiting right after.
      await Process.start(
        'explorer',
        ['/select,', path],
        mode: ProcessStartMode.detached,
      );
    } catch (e) {
      _log('Reveal in Explorer failed: $path: $e', level: 'ERROR');
    }
  }

  Future<void> _activateSelected({bool location = false}) async {
    if (_results.isNotEmpty && _selectedIndex >= 0) {
      final path = _results[_selectedIndex].path;
      _log('Activate index=$_selectedIndex location=$location path=$path');
      if (location) {
        await _openFileLocation(path);
      } else {
        await _openFile(path);
      }
    }
    _hideOverlay();
  }

  Future<void> _activateResult(int index) async {
    final location = HardwareKeyboard.instance.isShiftPressed;
    _log('Activate result $index location=$location');
    if (location) {
      await _openFileLocation(_results[index].path);
    } else {
      await _openFile(_results[index].path);
    }
    _hideOverlay();
  }

  // Keyboard
  static final Set<LogicalKeyboardKey> _dismissKeys = {
    // Ctrl is the summon hotkey (triple-Ctrl), so it must not dismiss the
    // overlay while the chord is still being typed.
    LogicalKeyboardKey.meta,
    LogicalKeyboardKey.metaLeft,
    LogicalKeyboardKey.metaRight,
    // Function keys
    LogicalKeyboardKey.f1,
    LogicalKeyboardKey.f2,
    LogicalKeyboardKey.f3,
    LogicalKeyboardKey.f4,
    LogicalKeyboardKey.f5,
    LogicalKeyboardKey.f6,
    LogicalKeyboardKey.f7,
    LogicalKeyboardKey.f8,
    LogicalKeyboardKey.f9,
    LogicalKeyboardKey.f10,
    LogicalKeyboardKey.f11,
    LogicalKeyboardKey.f12,
    // Navigation / editing keys
    LogicalKeyboardKey.pageUp,
    LogicalKeyboardKey.pageDown,
    LogicalKeyboardKey.home,
    LogicalKeyboardKey.end,
    LogicalKeyboardKey.insert,
    LogicalKeyboardKey.delete,
    LogicalKeyboardKey.printScreen,
    LogicalKeyboardKey.scrollLock,
    LogicalKeyboardKey.pause,
  };

  static bool _isDismissKey(LogicalKeyboardKey key) =>
      _dismissKeys.contains(key);

  KeyEventResult _handleTextFieldKey(FocusNode node, KeyEvent evt) {
    if (evt is! KeyDownEvent) return KeyEventResult.ignored;

    if (_isDismissKey(evt.logicalKey)) {
      _log('Dismiss key: ${evt.logicalKey}');
      _hideOverlay();
      return KeyEventResult.handled;
    }

    if (evt.logicalKey == LogicalKeyboardKey.escape) {
      _log('Dismiss key: Escape');
      _hideOverlay();
      return KeyEventResult.handled;
    }

    if (evt.logicalKey == LogicalKeyboardKey.enter) {
      _log('Enter pressed (shift=${HardwareKeyboard.instance.isShiftPressed})');
      _activateSelected(
        location: HardwareKeyboard.instance.isShiftPressed,
      );
      return KeyEventResult.handled;
    }

    if (evt.logicalKey == LogicalKeyboardKey.arrowDown) {
      if (_results.isNotEmpty) {
        setState(() {
          _selectedIndex = min(_selectedIndex + 1, _results.length - 1);
        });
        _scrollToSelected();
      }
      return KeyEventResult.handled;
    }

    if (evt.logicalKey == LogicalKeyboardKey.arrowUp) {
      if (_results.isNotEmpty) {
        setState(() {
          _selectedIndex = max(_selectedIndex - 1, 0);
        });
        _scrollToSelected();
      }
      return KeyEventResult.handled;
    }

    return KeyEventResult.ignored;
  }

  void _scrollToSelected() {
    if (!_scrollController.hasClients) return;
    final context = _itemKeys[_selectedIndex]?.currentContext;
    if (context != null) {
      Scrollable.ensureVisible(
        context,
        alignment: 0.5,
        duration: Duration.zero,
      );
    }
  }

  void _onOverlayKey(KeyEvent event) {
    if (event is! KeyDownEvent) return;
    if (_isDismissKey(event.logicalKey) ||
        event.logicalKey == LogicalKeyboardKey.escape) {
      _log('Overlay dismiss key: ${event.logicalKey}');
      _hideOverlay();
    }
  }

  // Compact status block shown under the search bar while the index is still
  // being built. Stays inside the panel so the spotlight look is preserved.
  Widget _buildNotReadyStatus() {
    final st = backendStatus;
    final indexing = st.phase == 'scan' || st.phase == 'extract';
    final String title;
    final String subtitle;
    if (indexing) {
      title = 'Indexing in progress';
      subtitle = '${st.found.toString()} found, '
          '${st.done.toString()} done'
          '${st.current.isNotEmpty ? ' - ${st.current}' : ''}';
    } else {
      title = 'Index not ready';
      subtitle = 'The PDF index is still being built. Please wait.';
    }
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 18),
      decoration: const BoxDecoration(
        color: Color(0xFFF8F8F8),
        borderRadius: BorderRadius.vertical(bottom: Radius.circular(7)),
      ),
      child: Column(
        children: [
          const Icon(Icons.hourglass_empty, size: 18, color: Colors.grey),
          const SizedBox(height: 8),
          Text(title,
              style: const TextStyle(fontSize: 13, color: Colors.black54)),
          const SizedBox(height: 4),
          Text(subtitle,
              style: const TextStyle(fontSize: 11, color: Colors.black38)),
        ],
      ),
    );
  }

  // Snippet
  String _makeSnippet(String text, String query) {
    if (text.isEmpty || query.isEmpty) return '';
    final idx = text.toLowerCase().indexOf(query.toLowerCase());
    if (idx == -1) return '';
    const context = 80;
    final start = max(0, idx - context ~/ 2);
    final end = min(text.length, idx + query.length + context ~/ 2);
    var snip = text.substring(start, end).replaceAll('\n', ' ').trim();
    if (start > 0) snip = '\u2026$snip';
    if (end < text.length) snip = '$snip\u2026';
    return snip;
  }

  @override
  Widget build(BuildContext context) {
    // Always render the transparent spotlight overlay. Returning an opaque
    // widget here (e.g. a bare status tile) makes the fullscreen window paint
    // white instead of transparent. The status is shown as a compact panel
    // under the search bar when the index isn't ready.
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: FadeTransition(
        opacity: _fadeAnimation,
        child: KeyboardListener(
          focusNode: _overlayFocusNode,
          onKeyEvent: _onOverlayKey,
          child: GestureDetector(
            onTap: _hideOverlay,
            child: Container(
              color: Colors.black12,
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Center(
                    // Prevent taps inside the panel from closing the search bar.
                    child: GestureDetector(
                      onTap: () {},
                      child: Container(
                        width: MediaQuery.sizeOf(context).width * 0.35,
                        decoration: BoxDecoration(
                          color: Colors.white.withValues(alpha: 0.95),
                          borderRadius: BorderRadius.circular(7),
                          boxShadow: [
                            BoxShadow(
                              color: Colors.black,
                              blurRadius: 10,
                              offset: Offset(0, 0),
                            ),
                          ],
                        ),
                        clipBehavior: Clip.antiAlias,
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            // Search bar: full-width row, input left, icon right
                            Padding(
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 14, vertical: 12),
                              child: Row(
                                children: [
                                  Expanded(
                                    child: TextField(
                                      controller: _searchController,
                                      focusNode: _textFocusNode,
                                      autofocus: true,
                                      style: const TextStyle(
                                          fontSize: 15, color: Colors.black87),
                                      decoration: const InputDecoration(
                                        hintText:
                                            'What file are you looking for?',
                                        border: InputBorder.none,
                                        isDense: true,
                                        isCollapsed: true,
                                        contentPadding: EdgeInsets.zero,
                                      ),
                                    ),
                                  ),
                                  const Icon(Icons.search,
                                      size: 20, color: Colors.black),
                                ],
                              ),
                            ),
                            if (!_dbReady)
                              _buildNotReadyStatus()
                            else if (_results.isNotEmpty) ...[
                              // Thin divider between search bar and results.
                              const Divider(height: 1, thickness: 1),
                              // Scrollable results list
                              ConstrainedBox(
                                constraints:
                                    const BoxConstraints(maxHeight: 300),
                                child: Scrollbar(
                                  controller: _scrollController,
                                  thumbVisibility: true,
                                  thickness: 4,
                                  radius: const Radius.circular(2),
                                  child: ListView.builder(
                                    controller: _scrollController,
                                    itemCount: _results.length,
                                    shrinkWrap: true,
                                    padding: EdgeInsets.zero,
                                    itemBuilder: (context, index) {
                                      final result = _results[index];
                                      final query =
                                          _searchController.text.trim();

                                      final snippet = result.rank <= 3
                                          ? _makeSnippet(
                                              result.fullText, query)
                                          : '';

                                      final itemKey = _itemKeys.putIfAbsent(
                                          index, () => GlobalKey());

                                      return GestureDetector(
                                        key: itemKey,
                                        onTap: () => _activateResult(index),
                                        child: Container(
                                          // Full-bleed highlight for the
                                          // selected row.
                                          color: index == _selectedIndex
                                              ? Colors.grey.shade300
                                              : Colors.white,
                                          padding: const EdgeInsets.symmetric(
                                              horizontal: 12, vertical: 12),
                                          child: Row(
                                            // Icon top-aligned: rows span
                                            // multiple lines of text.
                                            crossAxisAlignment:
                                                CrossAxisAlignment.start,
                                            children: [
                                              const Icon(
                                                  Icons.picture_as_pdf,
                                                  size: 28,
                                                  color: Colors.red),
                                              const SizedBox(width: 12),
                                              Expanded(
                                                child: Column(
                                                  crossAxisAlignment:
                                                      CrossAxisAlignment.start,
                                                  children: [
                                                    // Filename (primary line)
                                                    RichText(
                                                      text: TextSpan(
                                                        style:
                                                            const TextStyle(
                                                                fontSize: 16,
                                                                color: Colors
                                                                    .black87,
                                                                fontWeight:
                                                                    FontWeight
                                                                        .w600),
                                                        children:
                                                            _highlightText(
                                                                result.filename,
                                                                query),
                                                      ),
                                                      maxLines: 1,
                                                      overflow:
                                                          TextOverflow.ellipsis,
                                                    ),
                                                    const SizedBox(height: 3),
                                                    // File location (secondary)
                                                    Text(
                                                      result.path,
                                                      style: const TextStyle(
                                                          fontSize: 13,
                                                          color: Colors
                                                              .black54),
                                                      maxLines: 1,
                                                      overflow:
                                                          TextOverflow.ellipsis,
                                                    ),
                                                    if (snippet.isNotEmpty) ...[
                                                      const SizedBox(height: 3),
                                                      // Content snippet
                                                      // (1-2 lines, capped)
                                                      RichText(
                                                        text: TextSpan(
                                                          style:
                                                              const TextStyle(
                                                                  fontSize: 13,
                                                                  color: Colors
                                                                      .black54,
                                                                  height: 1.3),
                                                          children:
                                                              _highlightText(
                                                                  snippet,
                                                                  query),
                                                        ),
                                                        maxLines: 2,
                                                        overflow: TextOverflow
                                                            .ellipsis,
                                                      ),
                                                    ],
                                                  ],
                                                ),
                                              ),
                                            ],
                                          ),
                                        ),
                                      );
                                    },
                                  ),
                                ),
                              ),
                            ],
                          ],
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
