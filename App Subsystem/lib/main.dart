import 'dart:async';
import 'package:flutter/material.dart';

import 'dart:math' as math;
import 'package:pedometer/pedometer.dart';
import 'package:sensors_plus/sensors_plus.dart';
import 'package:flutter/services.dart';

import 'package:supabase_flutter/supabase_flutter.dart';

import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';
import 'package:geolocator/geolocator.dart';

import 'dart:convert';
// import 'dart:io';
// import 'package:path_provider/path_provider.dart';

// import 'package:flutter/foundation.dart' show kIsWeb;
// import 'dart:html' as html show AnchorElement, Url, Blob;

import 'csv_export_stub.dart'
  if (dart.library.html) 'csv_export_web.dart'
  if (dart.library.io) 'csv_export_io.dart';



final supa = Supabase.instance.client;

const int kUwbLiveThresholdMs = 10000;

// Add these new classes at the top of your file, after imports:

/// ============================================================================
/// NEW: System Health Models
/// ============================================================================

enum SystemHealth { healthy, degraded, critical, offline }

class RoverHealthStatus {
  final bool uwbLive;
  final bool ultrasonicActive;
  final bool paired;
  final Duration? uwbAge;
  final String? uwbStatus; // "LIVE", "DELAYED", "OFFLINE"
  final DateTime lastUpdate;
  
  const RoverHealthStatus({
    required this.uwbLive,
    required this.ultrasonicActive,
    required this.paired,
    this.uwbAge,
    this.uwbStatus,
    required this.lastUpdate,
  });
  
  SystemHealth get overallHealth {
    if (!paired) return SystemHealth.offline;
    if (uwbLive && ultrasonicActive) return SystemHealth.healthy;
    if (uwbLive || ultrasonicActive) return SystemHealth.degraded;
    return SystemHealth.critical;
  }
}

class ObstacleSensorData {
  final int? frontCm;
  final int? leftCm;
  final int? rightCm;
  final int? backCm;
  final DateTime timestamp;
  
  const ObstacleSensorData({
    this.frontCm,
    this.leftCm,
    this.rightCm,
    this.backCm,
    required this.timestamp,
  });
  
  factory ObstacleSensorData.fromRow(Map<String, dynamic> row) {
    DateTime parseTs(dynamic v) {
      if (v == null) return DateTime.now();
      return DateTime.tryParse(v.toString()) ?? DateTime.now();
    }

    return ObstacleSensorData(
      frontCm: (row['ultra_front_cm'] as num?)?.toInt(),
      leftCm: (row['ultra_left_cm'] as num?)?.toInt(),
      rightCm: (row['ultra_right_cm'] as num?)?.toInt(),
      backCm: (row['ultra_back_cm'] as num?)?.toInt(),
      timestamp: parseTs(row['created_at']),
    );
  }
}

class RoverDiagnostics {
  final double? loopTimeMs;
  final double? cpuTemp;
  final int? uptime;
  final DateTime timestamp;
  
  const RoverDiagnostics({
    this.loopTimeMs,
    this.cpuTemp,
    this.uptime,
    required this.timestamp,
  });
  
  factory RoverDiagnostics.fromRow(Map<String, dynamic> row) {
    return RoverDiagnostics(
      loopTimeMs: (row['loop_time_ms'] as num?)?.toDouble(),
      cpuTemp: (row['cpu_temp'] as num?)?.toDouble(),
      uptime: row['uptime'] as int?,
      timestamp: DateTime.tryParse(
        row['updated_at']?.toString() ?? row['ts']?.toString() ?? '',
      ) ?? DateTime.now(),
    );
  }
}

/// ============================================================================
/// State Machine Enum
/// ============================================================================


enum MapMode { indoor, outdoor }

class OutdoorPose {
  final double latitude;
  final double longitude;

  const OutdoorPose({
    required this.latitude,
    required this.longitude,
  });
}


class RoverPose {
  final String floorId;
  final double xMeters;
  final double yMeters;

  const RoverPose({
    required this.floorId,
    required this.xMeters,
    required this.yMeters,
  });
}


class OutdoorCampusMapTab extends StatefulWidget {
  const OutdoorCampusMapTab({
    super.key,
    required this.followDistanceMeters,
    this.userPose,
    this.roverPose,
    this.onUserTap,
    this.gpsStatusText,
  });

  final String? gpsStatusText;
  final double followDistanceMeters;
  final LatLng? userPose;
  final LatLng? roverPose;
  final void Function(LatLng? point)? onUserTap;

  @override
  State<OutdoorCampusMapTab> createState() => _OutdoorCampusMapTabState();
}


class _OutdoorCampusMapTabState extends State<OutdoorCampusMapTab> {
  final MapController _mapController = MapController();

  static const LatLng tamuCenter = LatLng(30.6187, -96.3365);

  bool _showLegend = true;

  @override
  Widget build(BuildContext context) {
    return Stack(
      children: [
        FlutterMap(
          mapController: _mapController,
          options: MapOptions(
            initialCenter: tamuCenter,
            initialZoom: 15.5,
            onTap: (_, latLng) {
              widget.onUserTap?.call(latLng);
            },
          ),
          children: [
            TileLayer(
              urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
              userAgentPackageName: 'com.example.luggage_rover',
            ),

            MarkerLayer(
              markers: [
                if (widget.userPose != null)
                  Marker(
                    point: widget.userPose!,
                    width: 44,
                    height: 44,
                    child: const Icon(Icons.person_pin_circle,
                        color: Colors.blue, size: 40),
                  ),

                if (widget.roverPose != null)
                  Marker(
                    point: widget.roverPose!,
                    width: 44,
                    height: 44,
                    child: const Icon(Icons.luggage,
                        color: Colors.red, size: 36),
                  ),
              ],
            ),
          ],
        ),

        // 👇 PROMPT OVERLAY
        if (widget.userPose == null)
          Positioned(
            top: 20,
            left: 20,
            right: 20,
            child: Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: Colors.black.withOpacity(0.75),
                borderRadius: BorderRadius.circular(12),
              ),
              child: Text(
                widget.gpsStatusText ??
                    'Waiting for GPS... Tap map only if GPS is unavailable',
                textAlign: TextAlign.center,
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
          ),

        if (widget.userPose != null)
          Positioned(
            top: 20,
            left: 20,
            right: 20,
            child: Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: Colors.black.withOpacity(0.70),
                borderRadius: BorderRadius.circular(12),
              ),
              child: Text(
                widget.gpsStatusText ??
                    'Blue marker = your current outdoor position. Tap "Refresh GPS" to clear it and wait for a new GPS fix.',
                textAlign: TextAlign.center,
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
          ),

        Positioned(
          bottom: 160,
          left: 20,
          child: FloatingActionButton.small(
            heroTag: 'legend_toggle',
            onPressed: () {
              setState(() {
                _showLegend = !_showLegend;
              });
            },
            child: Icon(
              _showLegend ? Icons.visibility_off : Icons.visibility,
            ),
          ),
        ),

        if (_showLegend)
          Positioned(
            bottom: 20,
            left: 20,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              decoration: BoxDecoration(
                color: Colors.black.withOpacity(0.75),
                borderRadius: BorderRadius.circular(12),
              ),
              child: const Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    'Map Legend',
                    style: TextStyle(
                      color: Colors.white,
                      fontWeight: FontWeight.bold,
                      fontSize: 13,
                    ),
                  ),
                  SizedBox(height: 8),
                  _MapLegendRow(
                    icon: Icons.person_pin_circle,
                    label: 'Your position',
                    iconColor: Colors.blue,
                  ),
                  SizedBox(height: 6),
                  _MapLegendRow(
                    icon: Icons.luggage,
                    label: 'Rover position',
                    iconColor: Colors.red,
                  ),
                  SizedBox(height: 6),
                  Text(
                    'Tap map to place a temporary position\nif GPS is unavailable.',
                    style: TextStyle(
                      color: Colors.white70,
                      fontSize: 11,
                    ),
                  ),
                ],
              ),
            ),
          ),
        // 👇 RESET BUTTON
        if (widget.userPose != null)
        Positioned(
          bottom: 90,
          right: 20,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              FloatingActionButton.small(
                heroTag: 'refresh_gps',
                backgroundColor: Colors.red,
                onPressed: () {
                  widget.onUserTap?.call(null);
                },
                child: const Icon(Icons.refresh),
              ),
              const SizedBox(height: 4),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: Colors.black.withOpacity(0.7),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: const Text(
                  'Refresh GPS',
                  style: TextStyle(
                    color: Colors.white,
                    fontSize: 11,
                  ),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}


class _MapLegendRow extends StatelessWidget {
  final IconData icon;
  final String label;
  final Color iconColor;

  const _MapLegendRow({
    required this.icon,
    required this.label,
    required this.iconColor,
  });

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, color: iconColor, size: 20),
        const SizedBox(width: 8),
        Text(
          label,
          style: const TextStyle(
            color: Colors.white,
            fontSize: 12,
          ),
        ),
      ],
    );
  }
}


enum RoverState {
  waitingForPair,
  idle,
  auto,
  manual,
  obstacleAvoid,
  hardStop,
  lostUwb,
  emergencyStop,
}


extension RoverStateExt on RoverState {
  String get displayName {
    switch (this) {
      case RoverState.waitingForPair: return 'Waiting for Pair';
      case RoverState.idle: return 'Idle';
      case RoverState.auto: return 'Auto Follow';
      case RoverState.manual: return 'Manual Control';
      case RoverState.obstacleAvoid: return 'Avoiding Obstacle';
      case RoverState.hardStop: return 'Hard Stop';
      case RoverState.lostUwb: return 'Lost Tracking';
      case RoverState.emergencyStop: return 'Emergency Stop';
    }
  }
  
  IconData get icon {
    switch (this) {
      case RoverState.waitingForPair: return Icons.link_off;
      case RoverState.idle: return Icons.pause_circle;
      case RoverState.auto: return Icons.autorenew;
      case RoverState.manual: return Icons.gamepad;
      case RoverState.obstacleAvoid: return Icons.rotate_left;
      case RoverState.hardStop: return Icons.pan_tool;
      case RoverState.lostUwb: return Icons.gps_off;
      case RoverState.emergencyStop: return Icons.stop_circle;
    }
  }
  
  Color get color {
    switch (this) {
      case RoverState.waitingForPair: return Colors.grey;
      case RoverState.idle: return Colors.blue;
      case RoverState.auto: return Colors.green;
      case RoverState.manual: return Colors.orange;
      case RoverState.obstacleAvoid: return Colors.amber;
      case RoverState.hardStop: return Colors.red;
      case RoverState.lostUwb: return Colors.deepOrange;
      case RoverState.emergencyStop: return Colors.red.shade900;
    }
  }
}

/// Phone-only PDR: updates (x,y) each step using current heading.
/// This is intentionally simple; good enough for demo UX.
class PdrPoseProvider {
  final String floorId;
  final double stepLenMeters;
  Offset _posMeters;            // floor coordinates in meters (x=left→right, y=top→down)
  double _headingRad = 0;       // 0 rad points along +X of your floor image
  StreamSubscription<StepCount>? _stepSub;
  StreamSubscription<MagnetometerEvent>? _magSub;
  final StreamController<UserPose> _ctrl = StreamController<UserPose>.broadcast();

  PdrPoseProvider({
    required this.floorId,
    required Offset startMeters,
    this.stepLenMeters = 0.75,
  }) : _posMeters = startMeters;

  Stream<UserPose> get stream => _ctrl.stream;

  Future<void> start() async {
    // heading from magnetometer (no tilt compensation — fine for demo)
    _magSub = magnetometerEventStream().listen(
      (MagnetometerEvent m) {
        // m.x, m.y, m.z are in µT
        _headingRad = math.atan2(m.y, m.x);
      },
      onError: (e) {
        // optional: fall back or log
        // _headingRad stays whatever it was
      },
      cancelOnError: false,
    );

    _stepSub = Pedometer.stepCountStream.listen((StepCount sc) {
      _posMeters = Offset(
        _posMeters.dx + stepLenMeters * math.cos(_headingRad),
        _posMeters.dy + stepLenMeters * math.sin(_headingRad),
      );
      _ctrl.add(UserPose(floorId: floorId, xMeters: _posMeters.dx, yMeters: _posMeters.dy));
    });

    // emit initial pose once so the dot shows immediately
    _ctrl.add(UserPose(floorId: floorId, xMeters: _posMeters.dx, yMeters: _posMeters.dy));
  }


  Future<void> stop() async {
    await _stepSub?.cancel();
    await _magSub?.cancel();
    await _ctrl.close();
  }

  void reset(Offset startMeters) {
    _posMeters = startMeters;
    _ctrl.add(
      UserPose(
        floorId: floorId,
        xMeters: _posMeters.dx,
        yMeters: _posMeters.dy,
      ),
    );
  }
}


const double _ft_to_m = 0.3048;
double ftToM(double ft) => ft * _ft_to_m;
double mToFt(double m) => m / _ft_to_m;


const double _lb_to_kg = 0.45359237;
double lbToKg(double lb) => lb * _lb_to_kg;
double kgToLb(double kg) => kg / _lb_to_kg;

enum WeightUnit { lb, kg }
String fmtLb(double kg) => '${kgToLb(kg).toStringAsFixed(1)} lb';
String fmtKg(double kg) => '${kg.toStringAsFixed(1)} kg';


void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  await SystemChrome.setPreferredOrientations([
    DeviceOrientation.portraitUp,
  ]);

  await Supabase.initialize(
    url: 'https://jbhjvtfeezmityewhtwa.supabase.co',
    anonKey: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImpiaGp2dGZlZXptaXR5ZXdodHdhIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjQ2NDUzNzgsImV4cCI6MjA4MDIyMTM3OH0.lFemSVezmA9to6SszkO9SvWioVgEwyELLSPqOgg-wBw',
  );

  runApp(const MyApp());
}



class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Luggage Rover',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color.fromARGB(255, 255, 0, 0)),
        useMaterial3: true,
      ),
      home: const RoverHome(),
    );
  }
}

/// ------------------------
/// Simple domain model (mock)
/// ------------------------
enum ConnStatus { disconnected, searching, connected }

class EventLogEntry {
  final DateTime time;
  final String message;
  EventLogEntry(this.message) : time = DateTime.now();
}

class ObstacleMarker {
  final String floorId;
  final Offset meters;      // floor coords in meters
  final DateTime time;
  const ObstacleMarker({required this.floorId, required this.meters, required this.time});
}


// Tutorial support
enum _TutorialAction { back, next, skip, done }

class _TutorialStep {
  final String title;
  final String body;
  final int? tabIndex; // which bottom tab to show for this step (0–4)

  const _TutorialStep({
    required this.title,
    required this.body,
    this.tabIndex,
  });
}

late final List<_TutorialStep> _tutorialSteps = [
  _TutorialStep(
    title: 'Welcome to Luggage Rover',
    body:
        'This short walkthrough will show you how to pair with your rover, '
        'view its location, and adjust safety settings. You can skip at any time.',
    tabIndex: 0,
  ),
  _TutorialStep(
    title: 'Connect Tab',
    body:
        'Here you secure-pair to the rover, reconnect if signal is lost, and see '
        'whether you are connected, searching, or disconnected.',
    tabIndex: 0,
  ),
  _TutorialStep(
    title: 'Rover Status',
    body:
        'This tab shows live distance, load weight, and whether the rover is moving, '
        'holding for an obstacle, or arrived at your destination.',
    tabIndex: 2,
  ),
  _TutorialStep(
    title: 'Map',
    body:
        'The Map tab shows your position on the map and where the rover '
        'lags behind you. Long-press on the map to set a new starting point if needed.',
    tabIndex: 3,
  ),
  _TutorialStep(
    title: 'Settings & Accessibility',
    body:
        'Here you can adjust text size, high-contrast mode, hallway overlays, units, and '
        'trigger test alerts such as 6 ft separation or weight change.',
    tabIndex: 4,
  ),
  _TutorialStep(
    title: 'Event Log',
    body:
        'The Log tab keeps a time-stamped list of connection changes, alerts, and other '
        'important events so you can review what happened.',
    tabIndex: 5,
  ),
  _TutorialStep(
    title: 'You\'re ready!',
    body:
        'You can restart this tutorial from the Settings tab any time.\n\n'
        'Stay within 6 ft for safe follow, watch the map for your route, and let Luggage Rover do the heavy lifting.',
    tabIndex: 1, // or wherever you want to leave them
  ),
];


Offset clampToHallwaysMeters(Offset p, FloorPlan floor) {
  if (floor.hallSegments.isEmpty) return p;

  Offset bestPoint = p;
  double bestDist2 = double.infinity;

  for (final seg in floor.hallSegments) {
    final ap = p - seg.a;
    final ab = seg.b - seg.a;
    final abLen2 = ab.dx * ab.dx + ab.dy * ab.dy;
    if (abLen2 == 0) continue;

    double t = (ap.dx * ab.dx + ap.dy * ab.dy) / abLen2;
    t = t.clamp(0.0, 1.0);

    final proj = Offset(
      seg.a.dx + ab.dx * t,
      seg.a.dy + ab.dy * t,
    );

    final dx = proj.dx - p.dx;
    final dy = proj.dy - p.dy;
    final d2 = dx * dx + dy * dy;

    if (d2 < bestDist2) {
      bestDist2 = d2;
      bestPoint = proj;
    }
  }

  return bestPoint;
}


class _PathPoint {
  final Offset position;
  final DateTime timestamp;
  
  const _PathPoint(this.position, this.timestamp);
  
  double get ageSeconds => DateTime.now().difference(timestamp).inMilliseconds / 1000.0;
}

/// ------------------------
/// App Home (state lives here)
/// ------------------------
class RoverHome extends StatefulWidget {
  const RoverHome({super.key});
  @override
  State<RoverHome> createState() => _RoverHomeState();


}

enum _PairingState { unpaired, inProgress, paired }

class _RoverHomeState extends State<RoverHome> {


  late final DateTime _appStartedAt = DateTime.now();
  
  WeightUnit displayUnit = WeightUnit.lb; // default UI in pounds

  String _currentRoverMode = 'auto'; // Track current mode
  double leftSpeedCmd = 0.0;
  double rightSpeedCmd = 0.0;
  double speedCmdAvg = 0.0;
  double speedCmdAbs = 0.0;

  MapMode _mapMode = MapMode.indoor;


  // optional smoothing
  double _speedAbsEma = 0.0;
  final double _speedEmaAlpha = 0.25;

  StreamSubscription<List<Map<String, dynamic>>>? _navStateSub;
  StreamSubscription<List<Map<String, dynamic>>>? _liveStateSub;
  StreamSubscription<List<Map<String, dynamic>>>? _obstacleDebugSub;


  // Core app state
  ConnStatus status = ConnStatus.disconnected;
  bool paired = false;
  bool autoReconnect = true;
  bool _testOverride = false; // bypass UWB/connection checks for bench testing
  bool _weightAlarmOverrideEnabled = false; // Mute load-cell/weight alarms
  // Weight threshold below which rover stops (mirrors LUGGAGE_WEIGHT_THRESHOLD_KG_DEFAULT=1.0)
  double _luggageWeightThresholdKg = 1.0;
  bool _luggageFallen = false; // synced from telemetry_snapshots.luggage_fallen
  bool _uwbOverrideEnabled = false; // Allow manual drive without UWB
  bool _manualOverrideModeEnabled = false; // Disable avoidance in manual mode
  bool _obstacleAvoidEnabled = true; // Obstacle avoidance active in all modes
  bool _startupTarePromptShown = false;
  bool _tareCompletedForSession = false;
  bool _luggageReadyMonitoringEnabled = false;
  bool _noLuggagePromptActive = false;
  DateTime? _lastNoLuggagePromptAt;
  bool _gpsResetInProgress = false;
  DateTime? _gpsResetAt;
  DateTime? _pairedAt;
  static const Duration _postPairGrace = Duration(seconds: 5);

  // Pairing state
  _PairingState _pairingState = _PairingState.unpaired;
  String? _sessionToken;        // UUID stored after successful pairing
  String? _userCode;            // 6-digit human-readable session identifier
  Timer? _pairingTimeoutTimer;
  Timer? _diagnosticsUiTimer;
  Timer? _obstaclePollTimer;
  Timer? _telemetryPollTimer;
  StreamSubscription<List<Map<String, dynamic>>>? _pairingSub;
  static const Duration _pairingTimeout = Duration(minutes: 2);
  DateTime? _lastRoverSeenAt;
  Timer? _connectionWatchdogTimer;

  static const Duration _roverOfflineAfter = Duration(seconds: 4);
  static const Duration _roverDisconnectedAfter = Duration(seconds: 10);


  String? _dbStateRaw;
  DateTime? _dbStateUpdatedAt;


  // Tracking & notifications
  bool tracking = true;
  bool notificationsEnabled = true;
  bool autoFloorDetect = true; // auto-switch Indoor Map floor from poseStream

  // Mock user pose stream (replace with your UWB feed)
  late Stream<UserPose> _poseStream;
  late final PdrPoseProvider _pdr;
  
  ElevatorZone? _activeElevator;

  int _tab = 0;
  String? _currentFloorId;

  DateTime? _lastElevatorPromptAt;
  String? _lastElevatorPromptKey;

  StreamSubscription<UserPose>? _poseSub;
  bool _elevatorDialogShowing = false;
  String? _insideElevatorKey; // "L1:L1_FRONT_ELEV" when inside

  bool _elevatorArmed = false;
  bool _skipFirstPoseForElevator = true;
  bool _suppressElevatorPrompts = false;
  final Map<String, int?> _lastHallSegIdxByFloor = {};




  // Rover telemetry
  double distanceMeters = 1.2; // live distance rover ↔ phone
  double weightKg = 0.0; // start 0 kg on the rover
  // Treat anything below this as "no luggage"
  static const double luggageThresholdKg = 1.0; // 1.0 kg
  bool get luggageLoaded => weightKg > luggageThresholdKg;
  bool get luggageOkForMotion => _weightAlarmOverrideEnabled || luggageLoaded;

  bool obstacleHold = false;
  bool arrived = false;
  bool separationActive = false;
  bool _lastObstacleHold = false;
  bool obstacleAvoidActive = false;     // rover is actively turning/reversing around obstacle
  bool _lastObstacleAvoidActive = false;
  double? _lastWeightKg;
  String? _lastObstacleReason;
  int _manualPrimedHoldSession = -1;
  UwbPosition? _uwbPos;
  StreamSubscription<List<Map<String, dynamic>>>? _uwbSub;

  DateTime _lastObstacleUiAt = DateTime.fromMillisecondsSinceEpoch(0);
  String? _lastObstacleUiMsg;
  final Duration _obstacleUiCooldown = const Duration(seconds: 2);

  // Optional: a dedicated toggle (so you can turn *just* these off)
  bool obstaclePopupsEnabled = false; // set true if you want them

  // Event log
  final List<EventLogEntry> logs = [];

  // Timers to simulate telemetry updates
  Timer? _telemetryTimer;

  StreamSubscription<List<Map<String, dynamic>>>? _telemetrySub;


  // accessibility / settings
  double textScale = 1.0;          // 1.0 = normal, 1.2 = large, 1.4 = extra large
  bool highContrast = false;       // simple high-contrast toggle
  bool showHallways = true;        // show/hide hallway overlay on map

  
  // --- Follow / separation settings ---
  final double maxSeparationMeters = ftToM(6); // 6 ft hard stop / alarm threshold
  double followDistanceMeters = ftToM(2);      // default = 2 ft

  // Discrete user options (1–4 ft)
  final List<double> _followFeetOptions = [1, 2, 3, 4];


  // Active session id (you can set this from the UI, or hard-code to start)
  SupabaseClient get supa => Supabase.instance.client;

  String? _activeSessionId;

  int _holdSession = 0;         // increments every time we start OR stop holding
  bool _isHolding = false;      // simple state gate
  Completer<Offset?>? _startPickCompleter;   // waits for long-press start
  Offset? _demoStartMeters;                 // snapped start (meters)


  // NEW: System Health & Diagnostics
  RoverHealthStatus? _healthStatus;
  ObstacleSensorData? _obstacleData;
  RoverDiagnostics? _diagnostics;
  StreamSubscription<List<Map<String, dynamic>>>? _healthSub;
  StreamSubscription<List<Map<String, dynamic>>>? _obstacleSub;
  StreamSubscription<List<Map<String, dynamic>>>? _diagnosticsSub;
  
  // NEW: State Machine
  RoverState _currentState = RoverState.waitingForPair;
  

  LatLng? _outdoorUserLatLng;
  LatLng? _outdoorRoverLatLng;

  LatLng? _prevOutdoorUserLatLng;
  double? _outdoorHeadingDeg;

  final Distance _geoDistance = const Distance();
  
  // NEW: Path trail history (last 10 seconds)
  final List<_PathPoint> _pathHistory = [];
  static const int _maxPathPoints = 50; // ~10s at 5Hz

  Stream<RoverPose> _roverPoseStreamFromSupabase() {
    final baseStream = supa
        .from('rover_pose_samples')
        .stream(primaryKey: ['id'])
        .order('ts', ascending: true);

    return baseStream.where((rows) => rows.isNotEmpty).map((rows) {
      final r = rows.last;
      return RoverPose(
        floorId: r['floor_id'] as String,
        xMeters: (r['rover_x_m'] as num).toDouble(),
        yMeters: (r['rover_y_m'] as num).toDouble(),
      );
    });
  }

  StreamSubscription<Position>? _phoneGpsSub;
  DateTime? _gpsStartWaitAt;
  DateTime? _gpsFirstFixAt;
  double? _gpsAccuracyM;
  bool _gpsWaitingForFirstFix = false;

  String _gpsStatusText() {
    if (_gpsResetInProgress && _gpsWaitingForFirstFix && _gpsStartWaitAt != null) {
      final s = DateTime.now().difference(_gpsStartWaitAt!).inSeconds;
      return 'GPS position was reset. Waiting for a fresh GPS fix... ${s}s\n'
          'You can also tap the map to place a temporary manual position.';
    }

    if (_gpsFirstFixAt != null && _gpsStartWaitAt != null) {
      final ms = _gpsFirstFixAt!.difference(_gpsStartWaitAt!).inMilliseconds;
      final acc = _gpsAccuracyM != null
          ? ' • accuracy ${_gpsAccuracyM!.toStringAsFixed(1)} m'
          : '';
      return 'Fresh GPS fix acquired in ${ms} ms$acc';
    }

    if (_gpsWaitingForFirstFix && _gpsStartWaitAt != null) {
      final s = DateTime.now().difference(_gpsStartWaitAt!).inSeconds;
      return 'Waiting for GPS fix... ${s}s\n'
          'If GPS is unavailable, tap the map to place a temporary manual position.';
    }

    return 'Showing your current outdoor GPS position.\n'
        'Use Reset GPS to clear this point and reacquire a fresh GPS fix.';
  }

  String _gpsDebugText = '';

  Future<void> _startPhoneGpsStream() async {
    await _debugGpsPermissionStatus();

    bool serviceEnabled = await Geolocator.isLocationServiceEnabled();
    if (!serviceEnabled) {
      _log('[GPS] Location services are disabled');
      return;
    }

    LocationPermission permission = await Geolocator.checkPermission();

    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
      _log('[GPS] requestPermission result: $permission');

      if (!mounted) return;
      setState(() {
        _gpsDebugText =
            'GPS service: ON | permission: $permission';
      });
    }

    if (permission == LocationPermission.denied) {
      _log('[GPS] Location permission denied by user');

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
              'Location permission was denied. Please allow location access for the app.',
            ),
            duration: Duration(seconds: 4),
          ),
        );
      }
      return;
    }

    if (permission == LocationPermission.deniedForever) {
      _log('[GPS] Location permission denied forever');

      if (!mounted) return;
      setState(() {
        _gpsDebugText =
            'Location permission denied forever. Open app settings to allow location.';
      });

      await Geolocator.openAppSettings();
      return;
    }

    if (!mounted) return;
    setState(() {
      _gpsStartWaitAt = DateTime.now();
      _gpsFirstFixAt = null;
      _gpsWaitingForFirstFix = true;
    });

    try {
      final lastKnown = await Geolocator.getLastKnownPosition();
      if (lastKnown != null && mounted) {
        final userLatLng = LatLng(lastKnown.latitude, lastKnown.longitude);
        _updateOutdoorUserPosition(userLatLng);
        setState(() {
          _gpsAccuracyM = lastKnown.accuracy;
        });
        _log('[GPS] Using last known position first');
      }
    } catch (e) {
      _log('[GPS] getLastKnownPosition error: $e');
    }

    try {
      final quickFix = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(
          accuracy: LocationAccuracy.medium,
        ),
      ).timeout(const Duration(seconds: 4));

      if (mounted) {
        final userLatLng = LatLng(quickFix.latitude, quickFix.longitude);
        _updateOutdoorUserPosition(userLatLng);
        setState(() {
          _gpsAccuracyM = quickFix.accuracy;
          _gpsFirstFixAt ??= DateTime.now();
          _gpsWaitingForFirstFix = false;
          _gpsResetInProgress = false;
        });
        _log('[GPS] First quick fix in '
            '${_gpsFirstFixAt!.difference(_gpsStartWaitAt!).inMilliseconds} ms');
      }
    } catch (e) {
      _log('[GPS] Quick fix timeout/error: $e');
    }

    _phoneGpsSub?.cancel();
    _phoneGpsSub = Geolocator.getPositionStream(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.medium,
        distanceFilter: 3,
      ),
    ).listen((pos) {
      if (!mounted) return;

      final userLatLng = LatLng(pos.latitude, pos.longitude);
      _updateOutdoorUserPosition(userLatLng);

      setState(() {
        _gpsAccuracyM = pos.accuracy;
        _gpsFirstFixAt ??= DateTime.now();
        _gpsWaitingForFirstFix = false;
        _gpsResetInProgress = false;
      });

      unawaited(
        supa.from('phone_gps_samples').insert({
          'session_token': _sessionToken,
          'latitude': pos.latitude,
          'longitude': pos.longitude,
          'accuracy_m': pos.accuracy,
          'heading_deg': _outdoorHeadingDeg,
          'ts': DateTime.now().toUtc().toIso8601String(),
        }),
      );
    }, onError: (e) {
      _log('[GPS] Phone stream error: $e');
    });
  }



  Future<void> _debugGpsPermissionStatus() async {
    final serviceEnabled = await Geolocator.isLocationServiceEnabled();
    final permission = await Geolocator.checkPermission();

    final msg =
        'GPS service: ${serviceEnabled ? "ON" : "OFF"} | permission: $permission';

    _log('[GPS] $msg');

    if (!mounted) return;

    setState(() {
      _gpsDebugText = msg;
    });

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(msg),
        duration: const Duration(seconds: 4),
      ),
    );
  }

  // Add this at the top of your _RoverHomeState class
  Future<T> _loggedSupabaseCall<T>(
    String operation,
    String table,
    Future<T> Function() call,
  ) async {
    _log('[DB] >>> $operation on $table');
    try {
      final result = await call();
      _log('[DB] <<< $operation on $table SUCCESS');
      return result;
    } catch (e, stack) {
      _log('[DB] XXX $operation on $table FAILED: $e');

      if (e is PostgrestException) {
        _log('[DB] code=${e.code} message=${e.message} details=${e.details} hint=${e.hint}');
      }

      _log('[DB] Stack: ${stack.toString().split('\n').take(8).join('\n')}');
      rethrow;
    }

  }


  void _startConnectionWatchdog() {
    _connectionWatchdogTimer?.cancel();

    _connectionWatchdogTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (!mounted) return;
      if (!paired) return;

      // NEW: grace period immediately after pairing
      if (_pairedAt != null &&
          DateTime.now().toUtc().difference(_pairedAt!) < _postPairGrace) {
        return;
      }

      final last = _lastRoverSeenAt;
      if (last == null) return;

      final age = DateTime.now().toUtc().difference(last);

      if (age >= _roverDisconnectedAfter) {
        if (status != ConnStatus.disconnected) {
          setState(() {
            status = ConnStatus.disconnected;
          });
          _log('[CONN] Rover disconnected (telemetry stale: ${age.inSeconds}s)');
        }
      } else if (age >= _roverOfflineAfter) {
        if (status != ConnStatus.searching) {
          setState(() {
            status = ConnStatus.searching;
          });
          _log('[CONN] Rover reconnecting… (telemetry stale: ${age.inSeconds}s)');
        }
      } else {
        if (status != ConnStatus.connected) {
          setState(() {
            status = ConnStatus.connected;
          });
          _log('[CONN] Rover connected');
        }
      }
    });
  }

  void _markRoverSeen([DateTime? ts]) {
    final seenAt = ts?.toUtc() ?? DateTime.now().toUtc();

    final wasNotConnected = status != ConnStatus.connected;

    if (!mounted) return;

    setState(() {
      _lastRoverSeenAt = seenAt;

      // Only update connection transport state here.
      // Do NOT change paired/session code here.
      status = ConnStatus.connected;
    });

    if (wasNotConnected) {
      _log('[CONN] Rover telemetry resumed');
    }
  }

  /// ============================================================================
  /// NEW: Health Monitoring Streams
  /// ============================================================================

  void _startHealthMonitoring() {
    // Calculate health status from existing UWB stream
    // _uwbSub?.cancel();
    _diagnosticsUiTimer = Timer.periodic(const Duration(milliseconds: 500), (_) {
      if (!mounted) return;
      setState(() {
        // Recompute health every tick based on current age of last known UWB pos
        if (_uwbPos != null) {
          final age = _uwbPos!.age;
          final isLive = age.inMilliseconds < kUwbLiveThresholdMs;
          final usLive = _obstacleData != null && DateTime.now().difference(_obstacleData!.timestamp).inMilliseconds < 3000;
          _healthStatus = RoverHealthStatus(
            uwbLive: isLive,
            ultrasonicActive: usLive,
            paired: paired,
            uwbAge: age,
            uwbStatus: isLive ? 'LIVE'
                : age.inSeconds < 5 ? 'DELAYED'
                : 'OFFLINE',
            lastUpdate: DateTime.now(),
          );
        } else if (_healthStatus == null) {
          // No UWB data at all yet → show offline immediately
          _healthStatus = RoverHealthStatus(
            uwbLive: false,
            ultrasonicActive: false,
            paired: paired,
            uwbAge: null,
            uwbStatus: 'OFFLINE',
            lastUpdate: DateTime.now(),
          );
        }
      });
    });
  }


  Future<void> _refreshObstacleSensors() async {
    try {
      final rows = await supa
          .from('obstacle_sensor_debug')
          .select(
            'id, created_at, ultra_front_cm, ultra_left_cm, ultra_right_cm, ultra_back_cm, obstacle_reason, obstacle_hold, obstacle_avoid_active, state',
          )
          .eq('robot_id', 'rover_01')
          .order('created_at', ascending: false)
          .limit(20);

      if (rows is! List || rows.isEmpty || !mounted) return;

      Map<String, dynamic>? latestValid;

      for (final raw in rows) {
        final row = Map<String, dynamic>.from(raw as Map);

        final hasAnyUltrasonic =
            row['ultra_front_cm'] != null ||
            row['ultra_left_cm'] != null ||
            row['ultra_right_cm'] != null ||
            row['ultra_back_cm'] != null;

        if (hasAnyUltrasonic) {
          latestValid = row;
          break;
        }
      }

      if (latestValid == null) {
        _log('[OBSTACLE SENSOR] no valid obstacle_sensor_debug row found');
        return;
      }

      _log(
        '[OBSTACLE SENSOR][DEBUG TABLE] created_at=${latestValid['created_at']} '
        'F=${latestValid['ultra_front_cm']} '
        'L=${latestValid['ultra_left_cm']} '
        'R=${latestValid['ultra_right_cm']} '
        'B=${latestValid['ultra_back_cm']} '
        'reason=${latestValid['obstacle_reason']} '
        'hold=${latestValid['obstacle_hold']} '
        'avoid=${latestValid['obstacle_avoid_active']} '
        'state=${latestValid['state']}',
      );

      if (!mounted) return;
      setState(() {
        _obstacleData = ObstacleSensorData.fromRow(latestValid!);
        _lastObstacleReason = latestValid['obstacle_reason'] as String?;
        obstacleHold = (latestValid['obstacle_hold'] as bool?) ?? obstacleHold;
        obstacleAvoidActive =
            (latestValid['obstacle_avoid_active'] as bool?) ?? obstacleAvoidActive;

        final obstacleStateRaw = latestValid['state'] as String?;
        if (!_hasFreshDbState && obstacleStateRaw != null && obstacleStateRaw.isNotEmpty) {
          _dbStateRaw = obstacleStateRaw;
          _dbStateUpdatedAt = DateTime.tryParse(latestValid['created_at']?.toString() ?? '');
        }
      });

      _updateStateMachine();
    } catch (e) {
      _log('[OBSTACLE SENSOR][DEBUG TABLE] ERROR: $e');
    }
  }

  void _startObstacleSensorStream() {
    _obstacleDebugSub?.cancel();

    _obstacleDebugSub = supa
        .from('obstacle_sensor_debug')
        .stream(primaryKey: ['id'])
        .eq('robot_id', 'rover_01')
        .order('created_at', ascending: false)
        .listen((rows) {
      
      if (rows.isEmpty || !mounted) return;

      Map<String, dynamic>? latestValid;

      for (final raw in rows) {
        final row = Map<String, dynamic>.from(raw);

        final hasAnyUltrasonic =
            row['ultra_front_cm'] != null ||
            row['ultra_left_cm'] != null ||
            row['ultra_right_cm'] != null ||
            row['ultra_back_cm'] != null;

        if (hasAnyUltrasonic) {
          latestValid = row;
          break;
        }
      }

      if (latestValid == null) {
        _log('[OBSTACLE SENSOR][STREAM] no valid row found');
        return;
      }

      _log(
        '[OBSTACLE SENSOR][STREAM] created_at=${latestValid['created_at']} '
        'F=${latestValid['ultra_front_cm']} '
        'L=${latestValid['ultra_left_cm']} '
        'R=${latestValid['ultra_right_cm']} '
        'B=${latestValid['ultra_back_cm']}',
      );

      if (!mounted) return;
      setState(() {
        _obstacleData = ObstacleSensorData.fromRow(latestValid!);
        _lastObstacleReason = latestValid['obstacle_reason'] as String?;
        obstacleHold = (latestValid['obstacle_hold'] as bool?) ?? obstacleHold;
        obstacleAvoidActive =
            (latestValid['obstacle_avoid_active'] as bool?) ?? obstacleAvoidActive;

        final obstacleStateRaw = latestValid['state'] as String?;
        if (!_hasFreshDbState && obstacleStateRaw != null && obstacleStateRaw.isNotEmpty) {
          _dbStateRaw = obstacleStateRaw;
          _dbStateUpdatedAt = DateTime.tryParse(latestValid['created_at']?.toString() ?? '');
        }
      });

      _updateStateMachine();
    }, onError: (e) {
      _log('[OBSTACLE SENSOR][STREAM] ERROR: $e');
    });
  }

  void _startDiagnosticsStream() {
    _diagnosticsSub = Supabase.instance.client
        .from('robot_heartbeat')
        .stream(primaryKey: ['robot_id'])
        .eq('robot_id', 'rover_01')
        .listen((rows) {
      if (rows.isEmpty) return;
      if (!mounted) return;
      setState(() {
        _diagnostics = RoverDiagnostics.fromRow(rows.first);
      });

      _markRoverSeen(_diagnostics?.timestamp);
    });
  }

  /// ============================================================================
  /// NEW: State Machine Logic
  /// ============================================================================
  bool get _weightStopEnforced => !_weightAlarmOverrideEnabled;
  bool get _noLuggageDetected => weightKg <= luggageThresholdKg;


  String _normalizeMode(String? mode) {
    return (mode ?? '').trim().toLowerCase();
  }

  RoverState? _mapDbState(String? raw) {
    switch ((raw ?? '').trim().toUpperCase()) {
      case 'WAITING_FOR_PAIR':
        return RoverState.waitingForPair;
      case 'IDLE':
        return RoverState.idle;
      case 'AUTO':
        return RoverState.auto;
      case 'MANUAL':
      case 'MANUAL_OVERRIDE':
        return RoverState.manual;
      case 'OBSTACLE_AVOID':
        return RoverState.obstacleAvoid;
      case 'HARD_STOP':
        return RoverState.hardStop;
      case 'LOST_UWB':
        return RoverState.lostUwb;
      case 'EMERGENCY_STOP':
        return RoverState.emergencyStop;
      default:
        return null;
    }
  }

  bool get _hasFreshDbState {
    final ts = _dbStateUpdatedAt;
    if (ts == null) return false;
    return DateTime.now().toUtc().difference(ts).inSeconds < 3;
  }


  void _updateStateMachine() {
    RoverState newState;

    // First priority: trust rover_live_state.state if it is fresh.
    final dbState = _hasFreshDbState ? _mapDbState(_dbStateRaw) : null;
    if (dbState != null) {
      newState = dbState;
    } else {
      // Fallback only when DB state is missing/stale.
      if (!paired) {
        newState = RoverState.waitingForPair;
      } else if (_currentRoverMode == 'stop') {
        newState = RoverState.emergencyStop;
      } else if (_luggageFallen && !_weightAlarmOverrideEnabled) {
        newState = RoverState.hardStop;
      } else if (_uwbPos != null &&
          !_uwbPos!.isLive &&
          !_testOverride &&
          !_uwbOverrideEnabled) {
        newState = RoverState.lostUwb;
      } else if (separationActive) {
        newState = RoverState.hardStop;
      } else if (obstacleHold) {
        newState = RoverState.hardStop;
      } else if (obstacleAvoidActive) {
        newState = RoverState.obstacleAvoid;
      } else if (_currentRoverMode == 'manual') {
        newState = RoverState.manual;
      } else if (_currentRoverMode == 'auto') {
        newState = RoverState.auto;
      } else {
        newState = RoverState.idle;
      }
    }

    if (newState != _currentState) {
      _log(
        '[STATE] ${_currentState.displayName} → ${newState.displayName} '
        '(dbState=$_dbStateRaw fresh=$_hasFreshDbState mode=$_currentRoverMode '
        'hold=$obstacleHold avoid=$obstacleAvoidActive luggage=$_luggageFallen)',
      );
      setState(() => _currentState = newState);
    }
  }


  double _bearingBetween(LatLng a, LatLng b) {
    final lat1 = a.latitude * math.pi / 180.0;
    final lon1 = a.longitude * math.pi / 180.0;
    final lat2 = b.latitude * math.pi / 180.0;
    final lon2 = b.longitude * math.pi / 180.0;

    final dLon = lon2 - lon1;

    final y = math.sin(dLon) * math.cos(lat2);
    final x = math.cos(lat1) * math.sin(lat2) -
        math.sin(lat1) * math.cos(lat2) * math.cos(dLon);

    double bearing = math.atan2(y, x) * 180.0 / math.pi;
    bearing = (bearing + 360.0) % 360.0;
    return bearing;
  }

  LatLng _offsetBehindByBearing(
    LatLng user,
    double followDistanceMeters,
    double headingDeg,
  ) {
    const double earthRadiusM = 6378137.0;

    final double bearingRad = (headingDeg + 180.0) * math.pi / 180.0;
    final double lat1 = user.latitude * math.pi / 180.0;
    final double lon1 = user.longitude * math.pi / 180.0;
    final double angularDistance = followDistanceMeters / earthRadiusM;

    final double lat2 = math.asin(
      math.sin(lat1) * math.cos(angularDistance) +
          math.cos(lat1) * math.sin(angularDistance) * math.cos(bearingRad),
    );

    final double lon2 = lon1 +
        math.atan2(
          math.sin(bearingRad) * math.sin(angularDistance) * math.cos(lat1),
          math.cos(angularDistance) - math.sin(lat1) * math.sin(lat2),
        );

    return LatLng(
      lat2 * 180.0 / math.pi,
      lon2 * 180.0 / math.pi,
    );
  }

  void _updateOutdoorUserPosition(LatLng userPoint) {
    double headingDeg = _outdoorHeadingDeg ?? 0.0;

    if (_prevOutdoorUserLatLng != null) {
      final movedMeters = _geoDistance.as(
        LengthUnit.Meter,
        _prevOutdoorUserLatLng!,
        userPoint,
      );

      // only update heading if the user actually moved enough
      if (movedMeters > 1.0) {
        headingDeg = _bearingBetween(_prevOutdoorUserLatLng!, userPoint);
      }
    }

    final roverPoint = _offsetBehindByBearing(
      userPoint,
      followDistanceMeters,
      headingDeg,
    );

    if (!mounted) return;

    setState(() {
      _outdoorUserLatLng = userPoint;
      _outdoorRoverLatLng = roverPoint;
      _prevOutdoorUserLatLng = userPoint;
      _outdoorHeadingDeg = headingDeg;
    });
  }

  void _onOutdoorUserTap(LatLng? point) {
    if (!mounted) return;

    setState(() {
      if (point == null) {
        _outdoorUserLatLng = null;
        _outdoorRoverLatLng = null;
        _prevOutdoorUserLatLng = null;
        _outdoorHeadingDeg = null;

        _gpsResetInProgress = true;
        _gpsResetAt = DateTime.now();
        _gpsStartWaitAt = DateTime.now();
        _gpsFirstFixAt = null;
        _gpsWaitingForFirstFix = true;
      } else {
        _gpsResetInProgress = false;
        _updateOutdoorUserPosition(point);
      }
    });
  }

  /// ============================================================================
  /// NEW: Path History Tracking
  /// ============================================================================

  void _addPathPoint(Offset meters) {
    _pathHistory.add(_PathPoint(meters, DateTime.now()));
    
    // Keep only last 10 seconds
    _pathHistory.removeWhere((p) => p.ageSeconds > 10.0);
    
    // Also cap at max points
    while (_pathHistory.length > _maxPathPoints) {
      _pathHistory.removeAt(0);
    }
  }


  final List<Map<String, dynamic>> _validationRows = [];
  String _currentTestName = 'UNSPECIFIED_TEST';
  String _currentSessionId = 'default_session';
  DateTime? _testStartTime;

  bool get _isTestRunning => _currentTestName != 'UNSPECIFIED_TEST';

  static const Map<String, List<String>> _testSteps = {
    'Distance Control Step Response': [
      '1. Set follow distance to 35 cm in Controls tab.',
      '2. Position rover 1.5 m from you.',
      '3. Switch rover to AUTO mode.',
      '4. Stand still — rover should drive to ~35 cm and settle.',
      '5. Watch rover_telemetry.csv: distance_m vs target_distance_cm.',
      '6. Tap Stop Test when settled (or after ~30 s).',
    ],
    'Heading Control Step Response': [
      '1. Set rover to AUTO mode, facing you straight on.',
      '2. Side-step ~90° so you are beside the rover.',
      '3. Stand still — rover should pivot to face you.',
      '4. Watch rover_telemetry.csv: heading_deg vs target_heading_deg.',
      '5. Tap Stop Test once heading settles within ±5°.',
    ],
    'Follow-Me Mission Test': [
      '1. Set rover to AUTO mode.',
      '2. Walk a predefined path with at least 2 turns and 1 pause.',
      '3. Tap "Log Waypoint Reached" at each turn or pause.',
      '4. Observe rover stability and tracking continuity.',
      '5. Tap Stop Test when path is complete.',
    ],
    'LOST_UWB Failsafe': [
      '1. Ensure rover is in AUTO mode and UWB is LIVE.',
      '2. While rover is moving, shut off the UWB tag or kill SUPA_uwb_host.',
      '3. Verify rover stops — rover_events.csv should log STATE_LOST_UWB.',
      '4. To recover: enable UWB Override in Settings, or restart UWB host.',
      '5. Tap Stop Test once recovery is confirmed.',
    ],
    'Tracking Accuracy': [
      '1. Measure 3–5 ground-truth points in the test area (tape measure).',
      '2. Walk to each point and stand still for 2 seconds.',
      '3. Tap "Mark Ground-Truth Point" at each location.',
      '4. After all points: compare uwb_positions.csv x_m/y_m to measurements.',
      '5. Tap Stop Test when all points are marked.',
    ],
    'Secure Pairing Gate': [
      '1. Power rover with app NOT yet paired.',
      '2. Try to send movement commands from the Controls tab.',
      '3. Confirm rover does not move (state = WAITING_FOR_PAIR).',
      '4. rover_events.csv should log CMD_REJECTED_UNPAIRED.',
      '5. Pair normally, confirm commands now work.',
      '6. Tap Stop Test.',
    ],
    'Bounds Safety': [
      '1. Set rover to AUTO mode, following you at normal distance.',
      '2. Walk away slowly until distance exceeds 6 ft (~183 cm).',
      '3. Confirm rover stops — rover_events.csv logs STATE_HARD_STOP.',
      '4. Walk back within 6 ft, confirm rover resumes.',
      '5. Tap Stop Test.',
    ],
    'Obstacle Avoidance Override': [
      '1. Set rover to AUTO mode, following you.',
      '2. Place a ~1 ft³ obstacle in the rover\'s path.',
      '3. Confirm rover avoids or hard-stops (does not plow through).',
      '4. rover_events.csv logs STATE_OBSTACLE_AVOID or STATE_HARD_STOP.',
      '5. Use "Clear Obstacle Override" button if rover gets stuck.',
      '6. Tap Stop Test.',
    ],
    'Manual Override Mode': [
      '1. Switch rover to MANUAL OVERRIDE mode in Controls tab.',
      '2. Drive rover toward an obstacle.',
      '3. Confirm rover ONLY hard-stops (no avoidance maneuvers).',
      '4. rover_telemetry.csv: obstacle_avoid_active should stay False.',
      '5. Tap Stop Test.',
    ],
    'Weight Alarm Integration': [
      '1. Place payload on rover tray.',
      '2. Confirm weight reads above threshold in Controls tab.',
      '3. Suddenly remove the payload.',
      '4. Confirm buzzer activates and rover stops.',
      '5. rover_events.csv should log WEIGHT_ALARM_TRIGGERED.',
      '6. Tap Stop Test.',
    ],
    'Anchor Disruption': [
      '1. Ensure both anchors are connected and UWB is LIVE.',
      '2. Physically disconnect Anchor 1 USB.',
      '3. Observe rover response — uwb_events.csv logs ANCHOR1_DISCONNECTED.',
      '4. Reconnect Anchor 1, repeat for Anchor 2.',
      '5. Tap Stop Test after both anchors restored.',
    ],
    'UWB & Nav Pipeline Latency': [
      '1. Run the merged UWB + navigation script on the Pi:',
      '   python3 SUPA_uwb_nav.py --test-name UWB_Nav_Pipeline_Latency',
      '2. Confirm UWB LIVE badge appears in the app (green).',
      '3. Wait 30 s — the app automatically records a latency row every time',
      '   a new uwb_positions update arrives from the database.',
      '4. Each row captures: uwb_published_at (Pi write time),',
      '   app_received_at (Flutter stream callback time), and',
      '   pipeline_latency_ms (difference, i.e. DB → app round-trip).',
      '5. Tap Stop Test, then Export App Validation CSV.',
      '6. Open the CSV and check pipeline_latency_ms — target < 300 ms.',
    ],
  };

  void _logValidationEvent({
    required String eventType,
    double? groundTruthX,
    double? groundTruthY,
    String? notes,
    // latency fields (populated by UWB stream during pipeline latency test)
    String? uwbPublishedAt,
    String? appReceivedAt,
    int? pipelineLatencyMs,
  }) {
    _validationRows.add({
      'ts_iso': DateTime.now().toUtc().toIso8601String(),
      'session_id': _currentSessionId,
      'test_name': _currentTestName,
      'event_type': eventType,
      'paired': paired,
      'mode': _currentRoverMode,
      'ground_truth_x_m': groundTruthX,
      'ground_truth_y_m': groundTruthY,
      'notes': notes ?? '',
      'source_ts_iso': uwbPublishedAt ?? '',
      'db_written_at': '',
      'app_received_at': appReceivedAt ?? '',
      'source_to_db_ms': '',
      'db_to_app_ms': '',
      'pipeline_latency_ms': pipelineLatencyMs,
    });

    _log('[VALIDATION] $eventType ${notes ?? ""}');
  }

  
  void _logDbPipelineLatency({
    required String tableName,
    required Map<String, dynamic> row,
    required DateTime appReceivedAt,
  }) {
    if (_currentTestName != 'UWB & Nav Pipeline Latency') return;

    DateTime? parseTs(dynamic v) {
      if (v == null) return null;
      return DateTime.tryParse(v.toString())?.toUtc();
    }

    final sourceTs = parseTs(row['source_ts_iso']);
    final dbWrittenAt = parseTs(row['db_written_at']);
    final updatedAt = parseTs(row['updated_at']);
    final createdAt = parseTs(row['created_at']);

    final dbVisibleAt = dbWrittenAt ?? updatedAt ?? createdAt;

    final sourceToDbMs =
        (sourceTs != null && dbVisibleAt != null)
            ? dbVisibleAt.difference(sourceTs).inMilliseconds
            : null;

    final dbToAppMs =
        (dbVisibleAt != null)
            ? appReceivedAt.difference(dbVisibleAt).inMilliseconds
            : null;

    final endToEndMs =
        (sourceTs != null)
            ? appReceivedAt.difference(sourceTs).inMilliseconds
            : null;

    _validationRows.add({
      'ts_iso': appReceivedAt.toIso8601String(),
      'session_id': _currentSessionId,
      'test_name': _currentTestName,
      'event_type': '${tableName}_update',
      'paired': paired,
      'mode': _currentRoverMode,
      'ground_truth_x_m': null,
      'ground_truth_y_m': null,
      'notes': 'table=$tableName',
      'source_ts_iso': sourceTs?.toIso8601String() ?? '',
      'db_written_at': dbVisibleAt?.toIso8601String() ?? '',
      'app_received_at': appReceivedAt.toIso8601String(),
      'source_to_db_ms': sourceToDbMs,
      'db_to_app_ms': dbToAppMs,
      'pipeline_latency_ms': endToEndMs,
    });

    _log(
      '[LATENCY][$tableName] '
      'source_to_db_ms=${sourceToDbMs ?? "NA"} '
      'db_to_app_ms=${dbToAppMs ?? "NA"} '
      'end_to_end_ms=${endToEndMs ?? "NA"}',
    );
  }

  
  void _logValidationNote(String note) {
    _logValidationEvent(
      eventType: 'operator_note',
      notes: note,
    );
  }

  void _startValidationTest(String testName) {
    if (_isTestRunning) {
      showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('Test already running'),
          content: Text(
            '"$_currentTestName" is still active.\n\nStop it and start "$testName"?',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('Stop & Start New'),
            ),
          ],
        ),
      ).then((confirmed) {
        if (confirmed == true) _doStartTest(testName);
      });
      return;
    }
    _doStartTest(testName);
  }

  void _doStartTest(String testName) {
    final ts = DateTime.now();
    final safeTs = ts.toIso8601String().replaceAll(':', '-');

    if (!mounted) return;

    setState(() {
      _currentTestName  = testName;
      _currentSessionId = '${safeTs}_${testName.replaceAll(' ', '_')}';
      _testStartTime    = ts;
    });

    _logValidationEvent(
      eventType: 'test_start',
      notes: 'started_from_app',
    );

    final steps = _testSteps[testName] ?? ['Run the test, then tap Stop Test.'];
    final sessionShort = _currentSessionId.length > 30
        ? '${_currentSessionId.substring(0, 30)}…'
        : _currentSessionId;

    showDialog(
      context: context,
      barrierDismissible: true,
      builder: (ctx) => AlertDialog(
        title: Row(
          children: [
            const Icon(Icons.science, color: Colors.green),
            const SizedBox(width: 8),
            Expanded(
              child: Text('$testName started',
                  style: const TextStyle(fontSize: 16)),
            ),
          ],
        ),
        content: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: const Color(0xFF1E1E1E),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('Run on Pi:',
                        style: TextStyle(color: Colors.grey, fontSize: 11)),
                    const SizedBox(height: 4),
                    Text(
                      'python3 SUPA_uwb_host.py \\\n'
                      '  --test-name "${testName.replaceAll(' ', '_')}" \\\n'
                      '  --session-id "$sessionShort"\n\n'
                      'python3 NAVIAPP_movementtest.py \\\n'
                      '  --test-name "${testName.replaceAll(' ', '_')}" \\\n'
                      '  --session-id "$sessionShort"',
                      style: const TextStyle(
                        color: Colors.greenAccent,
                        fontFamily: 'monospace',
                        fontSize: 11,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 14),
              const Text('Steps:',
                  style: TextStyle(fontWeight: FontWeight.bold)),
              const SizedBox(height: 6),
              ...steps.map((s) => Padding(
                    padding: const EdgeInsets.only(bottom: 4),
                    child: Text(s, style: const TextStyle(fontSize: 13)),
                  )),
              const SizedBox(height: 10),
              const Text(
                '⚠ Tap "Export App Validation CSV" when done — '
                'data is lost if you forget.',
                style: TextStyle(fontSize: 12, color: Colors.orange),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Got it — start testing'),
          ),
        ],
      ),
    );
  }

  void _stopValidationTest({String? notes}) {
    if (!_isTestRunning) return;

    final stoppedName = _currentTestName;
    final rowCount = _validationRows.length;

    _logValidationEvent(
      eventType: 'test_stop',
      notes: notes ?? 'stopped_from_app',
    );

    if (!mounted) return;

    setState(() {
      _currentTestName = 'UNSPECIFIED_TEST';
      _testStartTime   = null;
    });

    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => AlertDialog(
        title: const Row(
          children: [
            Icon(Icons.check_circle, color: Colors.green),
            SizedBox(width: 8),
            Text('Test stopped'),
          ],
        ),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('"$stoppedName" complete.'),
            const SizedBox(height: 8),
            Text(
              '$rowCount app event rows logged.',
              style: const TextStyle(color: Colors.grey, fontSize: 13),
            ),
            const SizedBox(height: 12),
            Container(
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: Colors.orange.shade50,
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: Colors.orange.shade200),
              ),
              child: const Row(
                children: [
                  Icon(Icons.warning_amber, color: Colors.orange, size: 18),
                  SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'Export now — app event data lives in RAM and will be '
                      'lost if you close the app.',
                      style: TextStyle(fontSize: 12),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            const Text(
              'Python CSVs (rover_telemetry, rover_events, rover_latency) '
              'are already saved on the Pi automatically.',
              style: TextStyle(fontSize: 12, color: Colors.grey),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Skip export'),
          ),
          FilledButton.icon(
            icon: const Icon(Icons.download),
            label: const Text('Export Now'),
            onPressed: () {
              Navigator.pop(ctx);
              _exportValidationCsv();
            },
          ),
        ],
      ),
    );
  }


  Future<void> _exportValidationCsv() async {
    if (_validationRows.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('No validation rows to export')),
      );
      return;
    }

    final headers = [
      'ts_iso',
      'session_id',
      'test_name',
      'event_type',
      'paired',
      'mode',
      'ground_truth_x_m',
      'ground_truth_y_m',
      'notes',
      'source_ts_iso',
      'db_written_at',
      'app_received_at',
      'source_to_db_ms',
      'db_to_app_ms',
      'pipeline_latency_ms',
    ];

    final buffer = StringBuffer();
    buffer.writeln(headers.join(','));
    for (final row in _validationRows) {
      final values = headers.map((h) {
        final v = row[h]?.toString() ?? '';
        final escaped = v.replaceAll('"', '""');
        return '"$escaped"';
      }).join(',');
      buffer.writeln(values);
    }

    final ts = DateTime.now().toUtc().toIso8601String()
        .replaceAll(':', '-')
        .replaceAll('.', '-')
        .substring(0, 19);

    final safeName = _currentSessionId.isNotEmpty &&
            _currentSessionId != 'default_session'
        ? _currentSessionId.replaceAll(RegExp(r'[^\w\-]'), '_')
        : ts;

    final fileName = 'validation_app_events_$safeName.csv';
    final csvContent = buffer.toString();

    try {
      await exportCsvFile(csvContent, fileName: fileName);

      _log('[VALIDATION] CSV export requested: $fileName');
      if (!mounted) return;

      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('CSV export completed: $fileName')),
      );
    } catch (e) {
      _log('[VALIDATION] CSV export failed: $e');
      if (!mounted) return;

      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('CSV export failed: $e')),
      );
    }
  }

  Offset clampStickyToHallwaysMeters(
    Offset p,
    FloorPlan floor, {
    double stickThresholdMeters = 3.0, // tune: 2–4m usually good
  }) {
    if (floor.hallSegments.isEmpty) return p;

    Offset projectOn(int idx) {
      final seg = floor.hallSegments[idx];
      final ap = p - seg.a;
      final ab = seg.b - seg.a;
      final abLen2 = ab.dx * ab.dx + ab.dy * ab.dy;
      if (abLen2 == 0) return seg.a;

      double t = (ap.dx * ab.dx + ap.dy * ab.dy) / abLen2;
      t = t.clamp(0.0, 1.0);

      return Offset(
        seg.a.dx + ab.dx * t,
        seg.a.dy + ab.dy * t,
      );
    }

    // 1) try to stick to last chosen hallway segment for this floor
    final lastIdx = _lastHallSegIdxByFloor[floor.id];
    if (lastIdx != null &&
        lastIdx >= 0 &&
        lastIdx < floor.hallSegments.length) {
      final projLast = projectOn(lastIdx);
      final dLast = (projLast - p).distance;

      // If still reasonably close, keep snapping to same segment (prevents teleports)
      if (dLast <= stickThresholdMeters) {
        return projLast;
      }
    }

    // 2) otherwise choose the nearest segment normally
    int bestIdx = 0;
    Offset bestPoint = p;
    double bestDist2 = double.infinity;

    for (int i = 0; i < floor.hallSegments.length; i++) {
      final proj = projectOn(i);
      final dx = proj.dx - p.dx;
      final dy = proj.dy - p.dy;
      final d2 = dx * dx + dy * dy;
      if (d2 < bestDist2) {
        bestDist2 = d2;
        bestPoint = proj;
        bestIdx = i;
      }
    }

    _lastHallSegIdxByFloor[floor.id] = bestIdx;
    return bestPoint;
  }






  Timer? _holdTimer;

  // How often to send the command while holding (tweak 80–200ms)
  static const Duration _holdPeriod = Duration(milliseconds: 200);

  void _startHold(Future<void> Function(int session) tick) {
    _holdTimer?.cancel();
    _isHolding = true;
    final session = ++_holdSession;

    // fire immediately
    unawaited(tick(session));

    _holdTimer = Timer.periodic(_holdPeriod, (_) {
      unawaited(tick(session));
    });
  }

  Future<void> _stopHold({bool sendStop = true}) async {
    _holdTimer?.cancel();
    _holdTimer = null;

    _isHolding = false;
    _holdSession++; // invalidate any in-flight ticks

    // Always reset ramp state so next press starts fresh from 0, not stale max.
    _currentTurnSpeed = 0.0;
    _currentStraightSpeed = 0.0;

    if (sendStop) {
      await _sendStop();
    }
  }


  Future<void> _setObstacleAvoidEnabled(bool enabled) async {
    await _updateRoverCommand({'obstacle_avoid_enabled': enabled});

    if (!enabled) {
      // Clear any latched ultrasonic hold/avoid override already active on rover
      await _clearObstacleOverride();

      if (mounted) {
        setState(() {
          obstacleHold = false;
          obstacleAvoidActive = false;
          _lastObstacleReason = null;
        });
      }

      _log('[OBSTACLE] Avoidance OFF + cleared latched obstacle override');
    }
  }

  Future<void> _setObstacleParams({
    int? thresholdCm,
    int? clearMarginCm,
    String? action, // 'stop' or 'avoid'
  }) async {
    final updates = <String, dynamic>{};

    if (thresholdCm != null) updates['obstacle_threshold_cm'] = thresholdCm;
    if (clearMarginCm != null) updates['obstacle_clear_margin_cm'] = clearMarginCm;

    if (action != null) {
      if (action != 'stop' && action != 'avoid') {
        _log('[UI] Invalid obstacle_action: $action');
        return;
      }
      updates['obstacle_action'] = action;
    }

    if (updates.isNotEmpty) {
      await _updateRoverCommand(updates);
    }
  }

  bool get _hardStopActive {
    return _currentRoverMode == 'stop'
        || separationActive
        || obstacleHold
        || (_luggageFallen && !_weightAlarmOverrideEnabled);
  }

  DateTime? _lastHardStopSnackAt;
  void _showHardStopSnack([String msg = 'Hard-stop active — manual controls disabled']) {
    if (!mounted) return;
    final now = DateTime.now();
    if (_lastHardStopSnackAt != null &&
        now.difference(_lastHardStopSnackAt!).inMilliseconds < 1200) return;

    _lastHardStopSnackAt = now;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(msg)),
    );
  }


  String? _manualBlockReason() {
    if (!_testOverride && status != ConnStatus.connected) return 'Not connected to rover';
    if (_currentRoverMode == 'stop') return 'Emergency stop active';
    if (!_testOverride && !_uwbOverrideEnabled && (_uwbPos == null || !_uwbPos!.isLive)) {
      return 'UWB tracking lost (enable UWB Override in Settings to continue)';
    }
    if (!_testOverride && separationActive) return 'Separation hard-stop (> 6 ft)';
    if (!_testOverride && obstacleHold) return 'Obstacle hold active';
    if (!_testOverride && obstacleAvoidActive) return 'Avoidance manoeuvre in progress';
    if (!_testOverride && _luggageFallen && !_weightAlarmOverrideEnabled) {
      return 'Luggage weight below threshold — override in Settings to continue';
    }
    return null;
  }


  void _markConnectedIfNeeded([String src = '']) {
    if (!mounted) return;
    if (status == ConnStatus.connected) return;

    setState(() {
      // Supabase stream being active does NOT mean the user has paired.
      // paired is only set true in _onPairingConfirmed().
      status = ConnStatus.connected;
    });
    _log('[CONN] Supabase stream active $src');
  }


  bool _prevHardStopActive = false;

  Future<void> _resetManualAfterHardStopClears() async {
    // Only needed in manual mode
    if (_currentRoverMode != 'manual') return;

    _log('[SAFETY] Hard-stop cleared → reset manual speeds to 0');

    // Reset ramp variables so next hold starts from 0 (not the old speed)
    _currentTurnSpeed = 0.0;
    _currentStraightSpeed = 0.0;

    // Stop motors (keeps mode manual if you're in manual mode)
    await _sendStop();

    // Optional: also cancel any ongoing hold loop so it doesn't instantly ramp again
    // (especially if the user is still pressing)
    await _stopHold(sendStop: false);
  }

  // ------------------------ Demo
  bool _demoRunning = false;
  int _demoToken = 0; // cancellation token

  Completer<String?>? _floorPickCompleter;
  bool _demoSuppressNotifications = false;

  Future<void> _withElevatorPromptSuppressed(Future<void> Function() fn) async {
    _suppressElevatorPrompts = true;
    try {
      await fn();
    } finally {
      // small delay so the “arrival pose” doesn’t instantly re-trigger
      await Future.delayed(const Duration(milliseconds: 900));
      _suppressElevatorPrompts = false;
    }
  }


  void _startNavStateFromSupabase() {
    final stream = Supabase.instance.client
        .from('nav_state')
        .stream(primaryKey: ['robot_id'])
        .eq('robot_id', 'rover_01');

    _navStateSub = stream.listen((rows) {
      if (rows.isEmpty) return;
      _markConnectedIfNeeded('(nav_state)');
      final r = rows.first;

      final l = (r['left_speed_cmd'] as num?)?.toDouble() ?? 0.0;
      final rr = (r['right_speed_cmd'] as num?)?.toDouble() ?? 0.0;

      if (!mounted) return;

      setState(() {
        leftSpeedCmd = l;
        rightSpeedCmd = rr;

        speedCmdAvg = (leftSpeedCmd + rightSpeedCmd) / 2.0;

        final absNow = (l.abs() + rr.abs()) / 2.0;
        _speedAbsEma = _speedEmaAlpha * absNow + (1 - _speedEmaAlpha) * _speedAbsEma;
        speedCmdAbs = _speedAbsEma;
      });

      _updateStateMachine();
    });
  }

  
  // (Optional) If you want an explicit “stop demo” button to cancel waiting:
  void _stopDemo() {
    _demoToken++; // cancels any running loop
    _floorPickCompleter?.complete(null); // unblock if waiting
    _floorPickCompleter = null;
    if (mounted) setState(() => _demoRunning = false);
    _log('--- Demo stopped ---');
  }


  /// Stream UserPose from Supabase pose_samples table.
  Stream<UserPose> _poseStreamFromSupabase() {
    final baseStream = Supabase.instance.client
        .from('pose_samples')
        .stream(primaryKey: ['id'])
        .order('ts', ascending: true);

    // Only emit when there is at least one row.
    return baseStream.where((rows) => rows.isNotEmpty).map((rows) {
      debugPrint('pose_samples rows len: ${rows.length}');
      final r = rows.last;
      debugPrint('latest pose row: $r');

      return UserPose(
        floorId: r['floor_id'] as String,
        xMeters: (r['user_x_m'] as num).toDouble(),
        yMeters: (r['user_y_m'] as num).toDouble(),
      );
    });
  }


  Future<void> _clearPoseSamplesOnStartup() async {
    try {
      // Delete all rows from pose_samples.
      // We need a filter; this effectively deletes everything.
      await supa
          .from('pose_samples')
          .delete()
          .neq('id', 0);   // assumes id is never 0

      _log('[DB] Cleared pose_samples on startup');
    } catch (e) {
      _log('[DB] Error clearing pose_samples: $e');
    }
  }

  void _startLiveStateStream() {
    final stream = supa
        .from('rover_live_state')
        .stream(primaryKey: ['robot_id'])
        .eq('robot_id', 'rover_01');

    _liveStateSub?.cancel();
    _liveStateSub = stream.listen((rows) {
      if (rows.isEmpty || !mounted) return;

      final r = Map<String, dynamic>.from(rows.first as Map);

      final appReceivedAt = DateTime.now().toUtc();
      
      if (r['source_ts_iso'] != null || r['db_written_at'] != null || r['updated_at'] != null) {
        _logDbPipelineLatency(
          tableName: 'rover_live_state',
          row: r,
          appReceivedAt: appReceivedAt,
        );
      }

      final updatedAt = DateTime.tryParse(r['updated_at']?.toString() ?? '');

      if (updatedAt != null) {
        _markRoverSeen(updatedAt);
      }

      final isLiveSession = updatedAt != null &&
          DateTime.now().toUtc().difference(updatedAt).inSeconds < 10;

      if (!mounted) return;
      setState(() {
        if (isLiveSession) {
          weightKg = (r['weight_kg'] as num?)?.toDouble() ?? weightKg;
        }

        _currentRoverMode = _normalizeMode(r['mode'] as String?);
        _dbStateRaw = r['state'] as String?;
        _dbStateUpdatedAt = updatedAt;

        obstacleHold = (r['obstacle_hold'] as bool?) ?? obstacleHold;
        arrived = (r['arrived'] as bool?) ?? arrived;
        obstacleAvoidActive =
            (r['obstacle_avoid_active'] as bool?) ?? obstacleAvoidActive;
        _luggageFallen =
            (r['luggage_fallen'] as bool?) ?? _luggageFallen;
        _lastObstacleReason =
            r['obstacle_reason'] as String?;

        _log(
          '[LIVE_STATE] '
          'mode=${r['mode']} '
          'state=${r['state']} '
          'weight_kg=${r['weight_kg']} '
          'hold=${r['obstacle_hold']} '
          'avoid=${r['obstacle_avoid_active']} '
          'fresh=$isLiveSession '
          'updated_at=${r['updated_at']}',
        );
      });

      _updateStateMachine();
      _maybeShowNoLuggagePrompt();
    }, onError: (e) {
      _log('[LIVE_STATE] ERROR: $e');
    });
  }

  Future<void> _refreshTelemetry() async {
    try {
      final rows = await supa
          .from('telemetry_snapshots')
          .select(
            'id, robot_id, created_at, source_ts_iso, db_written_at, '
            'distance_meters, weight_kg, obstacle_hold, arrived, '
            'obstacle_avoid_active, state',
          )
          .eq('robot_id', 'rover_01')
          .order('created_at', ascending: false)
          .limit(1);

      if (rows is! List || rows.isEmpty || !mounted) return;

      final r = Map<String, dynamic>.from(rows.first as Map);

      final appReceivedAt = DateTime.now().toUtc();
      _logDbPipelineLatency(
        tableName: 'telemetry_snapshots',
        row: r,
        appReceivedAt: appReceivedAt,
      );

      _log(
        '[TELEM][POLL] robot_id=${r['robot_id']} '
        'distance_meters=${r['distance_meters']} '
        'weight_kg=${r['weight_kg']} '
        'created_at=${r['created_at']}',
      );

      if (!mounted) return;

      setState(() {
        final telemDistance =
          (r['distance_meters'] as num?)?.toDouble();

        if (_uwbPos == null || !_uwbPos!.isLive) {
          distanceMeters = telemDistance ?? distanceMeters;
        }

        final telemCreatedAt = DateTime.tryParse(
          r['created_at']?.toString() ?? '',
        );

        final telemFresh = telemCreatedAt != null &&
            DateTime.now().toUtc().difference(telemCreatedAt).inSeconds < 5;

        final telemWeight = (r['weight_kg'] as num?)?.toDouble();

        if (telemFresh && telemWeight != null) {
          weightKg = telemWeight;
        }

        obstacleHold = (r['obstacle_hold'] as bool?) ?? obstacleHold;
        arrived = (r['arrived'] as bool?) ?? arrived;
        obstacleAvoidActive =
            (r['obstacle_avoid_active'] as bool?) ?? false;

        final telemStateRaw = r['state'] as String?;
        if (!_hasFreshDbState && telemStateRaw != null && telemStateRaw.isNotEmpty) {
          _dbStateRaw = telemStateRaw;
          _dbStateUpdatedAt = telemCreatedAt;
        }
      });

      _maybeShowNoLuggagePrompt();
    } catch (e) {
      _log('[TELEM][POLL] ERROR: $e');
    }
  }

  void _startTelemetryFromSupabase() {
    _telemetryPollTimer?.cancel();

    unawaited(_refreshTelemetry());

    _telemetryPollTimer = Timer.periodic(
      const Duration(milliseconds: 800),
      (_) => unawaited(_refreshTelemetry()),
    );
  }


  void _startUwbStream() async {
    // 1) One-time fetch to verify SELECT permissions immediately
    try {
      final rows = await supa
          .from('rover_commands')
          .select('target_distance, weight_alarm_override, luggage_weight_threshold_kg')
          .eq('robot_id', 'rover_01')
          .limit(1);

      if ((rows as List).isNotEmpty) {
        final row = rows.first;

        final storedCm = (row['target_distance'] as num?)?.toDouble();
        if (storedCm != null && storedCm > 0) {
          setState(() => followDistanceMeters = storedCm / 100.0);
        }

        final storedOverride = row['weight_alarm_override'] as bool?;
        if (storedOverride != null) {
          setState(() => _weightAlarmOverrideEnabled = storedOverride);
        }

        final storedThreshold = (row['luggage_weight_threshold_kg'] as num?)?.toDouble();
        if (storedThreshold != null && storedThreshold > 0) {
          setState(() => _luggageWeightThresholdKg = storedThreshold);
        }
      }
    } catch (e) {
      _log('[UWB] initial select FAILED: $e');
    }

    // 2) Realtime stream
    final stream = Supabase.instance.client
        .from('uwb_positions')
        .stream(primaryKey: ['robot_id'])
        .eq('robot_id', 'rover_01');

    _uwbSub?.cancel();
    
    _uwbSub = stream.listen(
      (rows) {
        _log('[UWB] stream rows=${rows.length}');
        if (rows.isEmpty) return;

        final appReceivedAt = DateTime.now().toUtc();
        final pos = UwbPosition.fromRow(rows.first);
        _markRoverSeen(pos.updatedAt);

        if (!mounted) return;
        setState(() {
          _uwbPos = pos;
          if (pos.isLive) {
            distanceMeters = pos.distanceMeters;
          }
        });

        // ── Latency logging (only during the pipeline latency test) ──────────
        if (_currentTestName == 'UWB & Nav Pipeline Latency') {
          final publishedAt = pos.updatedAt;
          final latencyMs = appReceivedAt.difference(publishedAt).inMilliseconds;

          _logValidationEvent(
            eventType: 'uwb_update',
            uwbPublishedAt: publishedAt.toIso8601String(),
            appReceivedAt: appReceivedAt.toIso8601String(),
            pipelineLatencyMs: latencyMs,
            notes: 'dist=${pos.distanceMeters.toStringAsFixed(2)}m '
                'angle=${pos.angleDeg.toStringAsFixed(1)}deg',
          );

          _log('[LATENCY] pipeline=${latencyMs}ms '
              'published=${publishedAt.toIso8601String()} '
              'received=${appReceivedAt.toIso8601String()}');
        }
        // ─────────────────────────────────────────────────────────────────────

        _updateStateMachine();
      },
      onError: (err) {
        _log('[UWB] stream ERROR: $err');
      },
    );
  }


  StreamSubscription<List<Map<String, dynamic>>>? _alertsSub;

  // Update the method to store it
  void _startObstacleAlerts() {
    _alertsSub = Supabase.instance.client
      .from('rover_alerts')
      .stream(primaryKey: ['id'])
      .order('created_at', ascending: false)
      .listen((rows) {
        if (rows.isEmpty) return;
        final latest = rows.first;
        final msg = latest['message'] as String? ?? '';
        if (msg.isNotEmpty && mounted) {
          // Banner on Controls tab handles this now — just log it
          _log('[OBSTACLE] $msg');
        }
      });
  }



  void _triggerElevatorDialogFromAnywhere(FloorPlan floor, ElevatorZone zone) {
    if (!mounted) return;

    // Optional: don’t spam while a dialog is already open
    if (_elevatorDialogShowing) return;

    // ✅ Optional but recommended: switch to Map tab so user sees it
    setState(() => _tab = 3);

    // IMPORTANT: schedule after the frame so showDialog is safe
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      if (!mounted) return;
      if (_elevatorDialogShowing) return;

      _elevatorDialogShowing = true;
      try {
        await _handleElevatorNear(floor, zone);
      } finally {
        _elevatorDialogShowing = false;
      }
    });
  }


  Future<T?> _showScaledDialog<T>({
    required BuildContext context,
    required WidgetBuilder builder,
  }) {
    final media = MediaQuery.of(context);

    return showDialog<T>(
      context: context,
      builder: (context) {
        return MediaQuery(
          data: media.copyWith(
            textScaler: TextScaler.linear(textScale),
          ),
          child: Builder(builder: builder),
        );
      },
    );
  }



  // Demo
  // bool _demoRunning = false;
  final _obstaclesCtrl = StreamController<ObstacleMarker>.broadcast();
  Stream<ObstacleMarker> get obstaclesStream => _obstaclesCtrl.stream;

  void _pushObstacle(String floorId, Offset meters) {
    _obstaclesCtrl.add(ObstacleMarker(floorId: floorId, meters: meters, time: DateTime.now()));
    _log('[SENSORS] Obstacle detected at (${meters.dx.toStringAsFixed(1)}, ${meters.dy.toStringAsFixed(1)}) m on $floorId');
  }

  // double _extraLagMeters = 0.0; // 0 normally; bump during avoidance


  Stream<UserPose> _mockPose() async* {
    // simple loop moving across the map
    double x = 5, y = 5;
    double vx = 0.7, vy = 0.4;
    final dt = const Duration(milliseconds: 400);
    while (true) {
      await Future.delayed(dt);
      x += vx;
      y += vy;
      final f = _floors.first; // Floor 1
      if (x < 0 || x > f.widthMeters) vx = -vx;
      if (y < 0 || y > f.heightMeters) vy = -vy;
      yield UserPose(floorId: 'L1', xMeters: x.clamp(0, f.widthMeters), yMeters: y.clamp(0, f.heightMeters));
    }
  }

  @override
  void initState() {
    super.initState();

    // Log the client info
    _log('[INIT] Supabase initialized');
    _log('[INIT] Auth user: ${supa.auth.currentUser?.id ?? "anonymous"}');
    _log('[INIT] Auth role: ${supa.auth.currentUser?.role ?? "anon"}');
    debugPrint('SUPABASE URL = ${Supabase.instance.client.rest.url}');

  
    _currentFloorId = _floors.first.id;
    _startTelemetryFromSupabase();
    _startLiveStateStream();   // use rover_live_state for live status
    _startUwbStream();         // keep UWB live feed
    _log('App started');
    _startPhoneGpsStream();
    _initializeRoverCommands();
    _startConnectionWatchdog(); 
    _navStateSub?.cancel();
    _startNavStateFromSupabase(); // keep nav_state for wheel commands
    _startObstacleAlerts();
    _startHealthMonitoring();
    _startObstacleSensorStream();
    _startDiagnosticsStream();

    // setState(() {
    //   paired = true;
    //   status = ConnStatus.connected;
    // });

    Future.microtask(_clearPoseSamplesOnStartup);

    _poseStream = _poseStreamFromSupabase().asBroadcastStream(); // ← comment this out for now

    _poseSub = _poseStream.listen((pose) {
      if (_suppressElevatorPrompts) return;

      if (!mounted) return;
      if (!autoFloorDetect) return;

      if (!_elevatorArmed) return;

      // optional: if you want to ignore the first pose after arming
      if (_skipFirstPoseForElevator) {
        _skipFirstPoseForElevator = false;
        return;
      }


      final floor = _floors.firstWhere(
        (f) => f.id == pose.floorId,
        orElse: () => _floors.first,
      );

      if (floor.elevators.isEmpty) return;

      final user = clampStickyToHallwaysMeters(
        Offset(pose.xMeters, pose.yMeters),
        floor,
      );


      ElevatorZone? hit;
      for (final z in floor.elevators) {
        if ((user - z.centerMeters).distance <= z.radiusMeters) {
          hit = z;
          break;
        }
      }

      if (hit != null) {
        final key = '${floor.id}:${hit.id}';

        // Fire ONLY when entering the elevator zone
        if (_insideElevatorKey != key) {
          _insideElevatorKey = key;
          _triggerElevatorDialogFromAnywhere(floor, hit);
        }
      } else {
        // User left all elevator zones → re-arm prompt
        _insideElevatorKey = null;
      }

    });


    _diagnosticsUiTimer = Timer.periodic(const Duration(milliseconds: 500), (_) {
      if (!mounted) return;

      // Only repaint when it matters (keeps it cheap)
      if (_uwbPos != null || _healthStatus != null || status != ConnStatus.connected) {
        setState(() {});
      }
    });


    WidgetsBinding.instance.addPostFrameCallback((_) async {
      await Future.delayed(const Duration(seconds: 1)); // small delay to let initial UI settle
      if (!mounted) return;

      await _showStartupClearWeightPrompt();
      if (!mounted) return;

      await _startTutorial();
    });
  }

  Future<void> _insertPose({
    required String floorId,
    required double x,
    required double y,
  }) async {
    await supa.from('pose_samples').insert({
      'floor_id': floorId,
      'user_x_m': x,
      'user_y_m': y,
      'ts': DateTime.now().toIso8601String(),
    });
  }

  Future<void> _insertTelemetry({
    double? distanceM,
    double? weightKg,
    bool? obstacleHold,
    bool? arrived,
  }) async {
    await supa.from('telemetry_snapshots').insert({
      'robot_id': 'rover_01',
      'distance_meters': distanceM ?? distanceMeters,
      'weight_kg': weightKg ?? this.weightKg,
      'obstacle_hold': obstacleHold ?? this.obstacleHold,
      'arrived': arrived ?? this.arrived,
    });
  }

  // void _stopDemo() {
  //   _demoToken++; // cancels any running loop
  //   setState(() => _demoRunning = false);
  //   _log('--- Demo stopped ---');
  // }


  Future<void> _runL1toL3SupabaseDemo() async {
    if (_demoRunning) return;
    setState(() => _demoRunning = true);
    final myToken = ++_demoToken;

    _log('--- Demo: L1 ➜ L3 started (Supabase) ---');

    // Force follow distance to 4 ft for demo
    setState(() => followDistanceMeters = ftToM(4));

    // Clean slate
    await _clearPoseSamplesOnStartup();

    // Ensure elevator logic is armed
    _elevatorArmed = true;
    _skipFirstPoseForElevator = false;

    // Make sure we are on the Map tab (your Map tab index is 3)
    setState(() => _tab = 3);

    // 1) WAIT for user to long-press and pick a start position
    _log('[DEMO] Long-press on the map to set a starting position…');
    _startPickCompleter = Completer<Offset?>();
    final start = await _startPickCompleter!.future;

    if (!mounted || myToken != _demoToken) return;
    if (start == null) {
      _log('[DEMO] No start chosen. Ending demo.');
      setState(() => _demoRunning = false);
      return;
    }

    // Helpful points (your existing elevator centers)
    const l1Elev = Offset(84.768, 129.801); // L1_FRONT_ELEV
    const l3Elev = Offset(81.912, 140.020); // L3_FRONT_ELEV

    // Create a small helper to interpolate steps (helps the trail always build)
    List<Offset> lerpPath(Offset a, Offset b, int n) {
      return List.generate(n, (i) {
        final t = (i + 1) / n;
        return Offset(a.dx + (b.dx - a.dx) * t, a.dy + (b.dy - a.dy) * t);
      });
    }

    // Demo path: start -> elevator (with enough intermediate points to build trail)
    final l1Path = <Offset>[
      start,
      ...lerpPath(start, l1Elev, 12),
      l1Elev,
    ];

    // After elevator -> continue on L3
    final l3Path = <Offset>[
      l3Elev,
      ...lerpPath(l3Elev, const Offset(100.0, 65.0), 18),
      const Offset(100.0, 65.0),
    ];

    // Make timestamps strictly increasing even if inserts happen fast
    int _tsBumpMs = 0;
    String nextTs() {
      _tsBumpMs += 40; // 40ms bump per insert
      return DateTime.now().add(Duration(milliseconds: _tsBumpMs)).toIso8601String();
    }

    Future<void> step(String floorId, Offset p) async {
      if (!mounted || myToken != _demoToken) return;

      final floor = _floors.firstWhere((f) => f.id == floorId);
      final snapped = clampStickyToHallwaysMeters(p, floor);


      await supa.from('pose_samples').insert({
        'floor_id': floorId,
        'user_x_m': snapped.dx,
        'user_y_m': snapped.dy,
        'ts': nextTs(),
      });

      // (Optional) keep telemetry quiet so dialogs don’t steal the demo
      await _insertTelemetry(
        distanceM: followDistanceMeters, // rover “behaves” at follow distance
        obstacleHold: false,
        arrived: false,
      );

      await Future.delayed(const Duration(milliseconds: 300));
    }

    // 2) Walk on L1 toward elevator
    for (final p in l1Path) {
      if (myToken != _demoToken) return;
      await step('L1', p);
    }

    if (!mounted || myToken != _demoToken) return;

    // 3) WAIT for elevator confirmation (use your existing dialog flow)
    _floorPickCompleter = Completer<String?>();
    _log('[DEMO] Waiting for elevator selection…');

    final picked = await _floorPickCompleter!.future;
    _floorPickCompleter = null;

    if (!mounted || myToken != _demoToken) return;

    if (picked != 'L3') {
      _log('[DEMO] User did not choose L3. Ending demo.');
      setState(() => _demoRunning = false);
      return;
    }

    _log('[DEMO] User confirmed L3. Continuing…');

    // 4) Switch the map floor immediately (so IndoorMapTab is aligned)
    setState(() => _currentFloorId = 'L3');

    // 5) Insert L3 elevator pose + walk away on L3
    for (final p in l3Path) {
      if (myToken != _demoToken) return;
      await step('L3', p);
    }

    // 6) Arrive
    await _insertTelemetry(
      distanceM: followDistanceMeters,
      obstacleHold: false,
      arrived: true,
    );

    if (!mounted || myToken != _demoToken) return;

    _log('--- Demo: L1 ➜ L3 completed ---');
    setState(() => _demoRunning = false);

    setState(() {
      // choose what you want after demo:
      autoFloorDetect = true;
      _currentFloorId = null;

      // // OR leave it on L3 intentionally:
      // _currentFloorId = 'L3';
    });

  }





  @override
  void dispose() {
    _uwbSub?.cancel();
    _holdTimer?.cancel();
    _navStateSub?.cancel();
    _liveStateSub?.cancel();
    _alertsSub?.cancel();
    _connectionWatchdogTimer?.cancel();
    _diagnosticsUiTimer?.cancel();
    _poseSub?.cancel();
    _telemetryTimer?.cancel();
    _phoneGpsSub?.cancel();
    _telemetrySub?.cancel();
    _telemetryPollTimer?.cancel();
    _healthSub?.cancel();        // NEW
    _obstacleSub?.cancel();      // NEW
    _diagnosticsSub?.cancel();   // NEW
    _obstaclesCtrl.close();
    _obstacleDebugSub?.cancel();
    super.dispose();
  }



  void _onMapStartPicked(UserPose pose) {
    final floor = _floors.firstWhere(
      (f) => f.id == pose.floorId,
      orElse: () => _floors.first,
    );

    // ✅ Compute snapped FIRST
    final snapped = clampToHallwaysMeters(
      Offset(pose.xMeters, pose.yMeters),
      floor,
    );

    // ✅ If demo is waiting for a start pick, unblock it (now snapped exists)
    _startPickCompleter?.complete(snapped);
    _startPickCompleter = null;

    // Save for demo flows
    _demoStartMeters = snapped;

    // Save to Supabase so pose_stream can see it
    supa.from('pose_samples').insert({
      'floor_id': pose.floorId,
      'user_x_m': snapped.dx,
      'user_y_m': snapped.dy,
      'ts': DateTime.now().toIso8601String(),
    }).then((_) {
      _log('[DB] Start pose saved to Supabase');
    }).catchError((e) {
      _log('[DB] Error saving start pose: $e');
    });

    _log(
      'Start set on ${pose.floorId} at '
      '(${snapped.dx.toStringAsFixed(1)}, ${snapped.dy.toStringAsFixed(1)}) m',
    );

    if (_suppressElevatorPrompts) return;

    // 🔔 If start is inside an elevator zone, fire prompt
    if (autoFloorDetect && floor.elevators.isNotEmpty) {
      for (final z in floor.elevators) {
        final d = (snapped - z.centerMeters).distance;
        if (d <= z.radiusMeters) {
          unawaited(_handleElevatorNear(floor, z));
          break;
        }
      }
    }

    _elevatorArmed = true;
    _skipFirstPoseForElevator = false;
  }


  bool _luggageDialogOpen = false;

  bool _dialogOpen = false;

  void _maybeShowNoLuggagePrompt() {
    if (!mounted) return;

    // Only after successful pairing + tare for this session
    if (!paired) return;
    if (!_tareCompletedForSession) return;
    if (!_luggageReadyMonitoringEnabled) return;

    // Do not show if override is on
    if (_weightAlarmOverrideEnabled) return;

    // Only show when actually below threshold
    if (weightKg > _luggageWeightThresholdKg) return;

    unawaited(_showNoLuggagePrompt());
  }

  Future<void> _showStartupClearWeightPrompt() async {
    if (!mounted || _dialogOpen || _startupTarePromptShown) return;

    _dialogOpen = true;
    _startupTarePromptShown = true;

    await showDialog(
      context: context,
      barrierDismissible: false,
      builder: (_) => AlertDialog(
        title: const Text('Clear Rover Weight'),
        content: const Text(
          'Please remove all luggage or any weight from the rover tray.\n\n'
          'This allows the rover to tare the weight sensor correctly before use.'
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('OK'),
          ),
        ],
      ),
    );

    _dialogOpen = false;
  }

  Future<void> _showNoLuggagePrompt() async {
    if (!mounted || _dialogOpen || _noLuggagePromptActive) return;

    final now = DateTime.now();
    if (_lastNoLuggagePromptAt != null &&
        now.difference(_lastNoLuggagePromptAt!).inSeconds < 8) {
      return;
    }

    _dialogOpen = true;
    _noLuggagePromptActive = true;
    _lastNoLuggagePromptAt = now;

    await showDialog(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Add Luggage'),
        content: Text(
          'No luggage detected on the rover.\n\n'
          'Please place luggage on the rover above the set threshold '
          '(${displayUnit == WeightUnit.lb ? fmtLb(_luggageWeightThresholdKg) : fmtKg(_luggageWeightThresholdKg)}).',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('OK'),
          ),
        ],
      ),
    );

    _dialogOpen = false;
    _noLuggagePromptActive = false;
  }




  Future<void> _startTutorial([int index = 0]) async {
    if (!mounted || _tutorialSteps.isEmpty) return;

    final step = _tutorialSteps[index];

    // Switch to the tab for this step if specified.
    if (step.tabIndex != null && _tab != step.tabIndex) {
      setState(() => _tab = step.tabIndex!);
      // small delay to let the tab build before showing dialog (optional)
      await Future.delayed(const Duration(milliseconds: 50));
    }

    final isFirst = index == 0;
    final isLast = index == _tutorialSteps.length - 1;

    final action = await showDialog<_TutorialAction>(
      context: context,
      barrierDismissible: false,
      builder: (context) {
        return AlertDialog(
          title: Text(step.title),
          content: Text(step.body),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(_TutorialAction.skip),
              child: const Text('Skip'),
            ),
            if (!isFirst)
              TextButton(
                onPressed: () => Navigator.of(context).pop(_TutorialAction.back),
                child: const Text('Back'),
              ),
            FilledButton(
              onPressed: () => Navigator.of(context)
                  .pop(isLast ? _TutorialAction.done : _TutorialAction.next),
              child: Text(isLast ? 'Finish' : 'Next'),
            ),
          ],
        );
      },
    );

    if (!mounted || action == null) return;

    switch (action) {
      case _TutorialAction.skip:
      case _TutorialAction.done:
        // End tutorial
        return;
      case _TutorialAction.back:
        if (index > 0) {
          return _startTutorial(index - 1);
        }
        return; // already at first step
      case _TutorialAction.next:
        if (index + 1 < _tutorialSteps.length) {
          return _startTutorial(index + 1);
        }
        return;
    }
  }


  void _onManualFloorPick(String floorId) {
    if (!mounted) return;

    setState(() {
      autoFloorDetect = false;     // 👈 IMPORTANT: stop overriding
      _currentFloorId = floorId;   // 👈 make map match what user picked
    });
    _log('[MAP] Manual floor set to $floorId (auto floor detect OFF)');
  }


  Future<void> _handleElevatorNear(FloorPlan floor, ElevatorZone zone) async {
    if (!mounted) return;

    if (!autoFloorDetect) {
      _log('[ELEVATOR] Near ${zone.id} on ${floor.id} (auto floor detect OFF)');
      return;
    }

    // Make sure user can see the map while answering
    // Map tab index is 3 in your body list.
    if (_tab != 3) setState(() => _tab = 3);

    // Debounce: ignore repeated triggers for same elevator within 3 seconds
    final key = '${floor.id}:${zone.id}';
    final now = DateTime.now();

    if (_lastElevatorPromptKey == key &&
        _lastElevatorPromptAt != null &&
        now.difference(_lastElevatorPromptAt!).inSeconds < 3) {
      return;
    }

    _lastElevatorPromptKey = key;
    _lastElevatorPromptAt = now;

    setState(() {
      _activeElevator = zone;
      _currentFloorId = floor.id;
    });

    _log('[ELEVATOR] Near elevator ${zone.id} on ${floor.id}');

    final otherFloors = _floors.where((f) => f.id != floor.id).toList();

    final chosenId = await _showScaledDialog<String>(
      context: context,
      builder: (context) {
        return AlertDialog(
          title: const Text('Change floor?'),
          content: Text(
            'You are near the elevator on ${floor.name}.\n'
            'Did you ride to another level?',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: Text('Stay on ${floor.name}'),
            ),
            for (final f in otherFloors)
              FilledButton(
                onPressed: () => Navigator.pop(context, f.id),
                child: Text('Go to ${f.name}'),
              ),
          ],
        );
      },
    );

    // If the demo flow is waiting for a pick, unblock it
    _floorPickCompleter?.complete(chosenId);
    _floorPickCompleter = null;

    if (!mounted) return;

    // User stayed (or dismissed dialog)
    if (chosenId == null) {
      _log('[ELEVATOR] User stayed on ${floor.id}');
      setState(() => _activeElevator = null);
      return;
    }

    final destFloor = _floors.firstWhere(
      (f) => f.id == chosenId,
      orElse: () => floor,
    );

    await _withElevatorPromptSuppressed(() async {
      if (!mounted) return;

      setState(() {
        _currentFloorId = destFloor.id;
        _activeElevator = null;
      });

      _log('[ELEVATOR] Floor changed to ${destFloor.name} (${destFloor.id})');

      // Pick "matching" elevator on destination floor (FRONT->FRONT, BACK->BACK)
      ElevatorZone? destElevator;
      if (destFloor.elevators.isNotEmpty) {
        String suffix(String id) => id.split('_').skip(1).join('_');
        final srcSuffix = suffix(zone.id);

        destElevator = destFloor.elevators.firstWhere(
          (e) => suffix(e.id) == srcSuffix,
          orElse: () => destFloor.elevators.first,
        );
      }

      if (destElevator != null) {
        final snapped = clampStickyToHallwaysMeters(
          destElevator.centerMeters,
          destFloor,
        );

        await supa.from('pose_samples').insert({
          'floor_id': destFloor.id,
          'user_x_m': snapped.dx,
          'user_y_m': snapped.dy,
          'ts': DateTime.now().toIso8601String(),
        });

        _log('[DB] Elevator pose saved at '
            '(${snapped.dx.toStringAsFixed(1)}, ${snapped.dy.toStringAsFixed(1)}) m '
            'on ${destFloor.id}');
      }
    });

    if (!mounted) return;

    await _showScaledDialog<void>(
      context: context,
      builder: (context) {
        return AlertDialog(
          title: const Text('Confirm position'),
          content: Text(
            'We placed your position at the elevator on ${destFloor.name}.\n\n'
            'If this dot does not look right, long-press anywhere on the map '
            'to set a new starting position.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('OK'),
            ),
          ],
        );
      },
    );

    await _notify('Floor changed', 'Map switched to ${destFloor.name}.');
    setState(() => _activeElevator = null);
  }



  Future<void> _initializeRoverCommands() async {
    try {
      // NOTE: We do NOT include target_distance here.
      // Writing it on every startup would overwrite whatever the user last
      // set via the follow-distance picker. Instead the picker calls
      // _updateTargets(distance: ...) explicitly, which is the only place
      // that field should be written.
      //
      // We also use ignoreDuplicates so a re-pair / hot-restart never resets
      // fields that the rover or user already configured in this session.
      await supa.from('rover_commands').upsert({
        'robot_id': 'rover_01',
        'mode': 'auto',
        'manual_left_speed': 0.0,
        'manual_right_speed': 0.0,
        'manual_override_mode': false,
        'uwb_override_enabled': false,
        'emergency_stop': false,
        'reset_luggage': false,
        'command_version': 0,
        'obstacle_avoid_enabled': true,
        'obstacle_threshold_cm': 61,
        'obstacle_clear_margin_cm': 8,
        'obstacle_action': 'avoid',
        'clear_obstacle_override': false,
        'target_heading': 0.0,
        'luggage_weight_threshold_kg': _luggageWeightThresholdKg,
        if (_sessionToken != null) 'session_token': _sessionToken,
      }, onConflict: 'robot_id');
      _log('[DB] rover_commands initialised on startup (target_distance preserved)');

      // Read back whatever target_distance is already stored so our local
      // followDistanceMeters state matches the rover's current setpoint.
      final rows = await supa
          .from('rover_commands')
          .select('target_distance')
          .eq('robot_id', 'rover_01')
          .limit(1);
      if ((rows as List).isNotEmpty) {
        final storedCm =
            (rows.first['target_distance'] as num?)?.toDouble();
        if (storedCm != null && storedCm > 0) {
          setState(() => followDistanceMeters = storedCm / 100.0);
          _log('[DB] Restored follow distance from DB: '
              '${storedCm.toStringAsFixed(0)} cm');
        }
      }
    } catch (e) {
      _log('[ERROR] Failed to initialize rover_commands: $e');
    }
  }



  void _log(String msg) {
    setState(() => logs.insert(0, EventLogEntry(msg)));
  }

  Future<void> _notify(String title, String body) async {
    _log('$title: $body');

    // If weight override is enabled, suppress ONLY weight-related alarms/notifications.
    if (_weightAlarmOverrideEnabled && title.toLowerCase().contains('weight')) return;

    if (!notificationsEnabled) return;
    if (!mounted) return;

    await _showScaledDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(title),
        content: Text(body),
        actions: [
          TextButton(
            onPressed: () {
              _log('Notification dismissed: $title');
              Navigator.pop(context);
            },
            child: const Text('OK'),
          ),
        ],
      ),
    );
  }

  Future<void> _emitUltrasonicSequence(List<int> cm, {int ms = 150}) async {
    for (final v in cm) {
      _log('Front: $v cm');
      await Future.delayed(Duration(milliseconds: ms));
    }
  }



  /// ------------------------
  /// Pairing & connection flows
  /// ------------------------
  // ── Pairing ────────────────────────────────────────────────────────────────

  Future<void> _startPairing() async {
    if (_pairingState == _PairingState.inProgress) return;
    _logValidationEvent(eventType: 'pairing_start');

    if (!mounted) return;

    setState(() {
      _pairingState = _PairingState.inProgress;
      status = ConnStatus.searching;
    });
    _log('[PAIR] Pairing initiated by user');

    // Generate a new session token
    final token = _generateToken();
    final expiresAt = DateTime.now().toUtc()
        .add(_pairingTimeout)
        .toIso8601String();

    try {
      // Write the pairing request to Supabase (rover will see this and confirm)
      await supa.from('pairing_sessions').upsert({
        'robot_id':      'rover_01',
        'session_token': token,
        'paired':        false,
        'expires_at':    expiresAt,
        'confirmed_at':  null,
        'user_code':     null,
      }, onConflict: 'robot_id');
    } catch (e) {
      _log('[PAIR] Failed to write pairing request: $e');
      _cancelPairing(reason: 'Could not reach server. Check your connection.');
      return;
    }

    // Start timeout
    _pairingTimeoutTimer?.cancel();
    _pairingTimeoutTimer = Timer(_pairingTimeout, () {
      if (_pairingState == _PairingState.inProgress) {
        _cancelPairing(reason: 'Pairing timed out — is the rover powered on?');
      }
    });

    // Watch for rover to confirm
    _watchPairingSession(token);
  }

  DateTime? _pairingFalseSeenAt;
  static const Duration _pairingFalseGrace = Duration(seconds: 3);

  void _watchPairingSession(String expectedToken) {
    _pairingSub?.cancel();
    _pairingSub = supa
        .from('pairing_sessions')
        .stream(primaryKey: ['robot_id'])
        .eq('robot_id', 'rover_01')
        .listen((rows) {
      if (rows.isEmpty) return;
      final row = rows.first;

      final isPaired = row['paired'] as bool? ?? false;
      final returnToken = row['session_token'] as String? ?? '';

      if (isPaired && returnToken == expectedToken) {
        _pairingFalseSeenAt = null;

        if (_pairingState != _PairingState.paired) {
          _onPairingConfirmed(expectedToken);
        }
        return;
      }

      if (!isPaired && _pairingState == _PairingState.paired) {
        final now = DateTime.now().toUtc();
        _pairingFalseSeenAt ??= now;

        if (now.difference(_pairingFalseSeenAt!) < _pairingFalseGrace) {
          return;
        }

        _log('[PAIR] Rover cleared session — resetting to unpaired.');

        if (!mounted) return;
        setState(() {
          _pairingState = _PairingState.unpaired;
          _sessionToken = null;
          _userCode = null;
          paired = false;
          status = ConnStatus.disconnected;
        });

        if (autoReconnect) {
          _log('[PAIR] Auto-reconnect enabled — starting new pairing in 2 s…');
          if (mounted) {
            ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
              content: Text('Rover signal lost — auto-reconnecting with new code…'),
              duration: Duration(seconds: 3),
            ));
          }

          Future.delayed(const Duration(seconds: 2), () {
            if (mounted && _pairingState == _PairingState.unpaired) {
              _startPairing();
            }
          });
        } else {
          if (mounted) {
            ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
              content: Text('Rover disconnected — tap Start Pairing to reconnect.'),
              duration: Duration(seconds: 5),
            ));
          }
        }
      } else {
        _pairingFalseSeenAt = null;
      }
    });
  }

  void _onPairingConfirmed(String token) {
    _pairingTimeoutTimer?.cancel();
    _pairingSub?.cancel();

    final code = _tokenToUserCode(token);
    final nowUtc = DateTime.now().toUtc();

    supa.from('pairing_sessions').update({
      'user_code': code,
    }).eq('robot_id', 'rover_01').then((_) {}).catchError((_) {});

    _logValidationEvent(
      eventType: 'pairing_confirmed',
      notes: 'session_code=$code',
    );

    if (!mounted) return;
    setState(() {
      _sessionToken = token;
      _userCode = code;
      _pairingState = _PairingState.paired;
      paired = true;
      status = ConnStatus.connected;

      // NEW
      _pairedAt = nowUtc;
      _lastRoverSeenAt = nowUtc;

      _tareCompletedForSession = false;
      _luggageReadyMonitoringEnabled = false;
    });

    _log('[PAIR] ✓ Paired — session code: $code');

    unawaited(_resetLuggage());

    Future.delayed(const Duration(seconds: 4), () {
      if (!mounted || !paired) return;

      setState(() {
        _tareCompletedForSession = true;
        _luggageReadyMonitoringEnabled = true;
      });

      _log('[WEIGHT] Tare complete — luggage monitoring enabled');
      _maybeShowNoLuggagePrompt();
    });

    Future.delayed(const Duration(seconds: 1), () {
      if (mounted && _pairingState == _PairingState.paired) {
        _watchPairingSession(token);
      }
    });
  }

  void _cancelPairing({String reason = 'Pairing cancelled'}) {
    _pairingTimeoutTimer?.cancel();
    _pairingSub?.cancel();
    if (!mounted) return;
    setState(() {
      _pairingState = _PairingState.unpaired;
      status        = ConnStatus.disconnected;
    });
    _log('[PAIR] $reason');
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(reason)),
      );
    }
  }

  void _repairRover() {
    if (!mounted) return;
    setState(() {
      _pairingState = _PairingState.unpaired;
      _sessionToken = null;
      _userCode     = null;
      paired        = false;
      status        = ConnStatus.disconnected;
      _tareCompletedForSession = false;
      _luggageReadyMonitoringEnabled = false;
      _noLuggagePromptActive = false;
    });
    _startPairing();
  }

  void _disconnect() {
    _pairingTimeoutTimer?.cancel();
    _pairingSub?.cancel();
    if (!mounted) return;
    setState(() {
      _pairingState = _PairingState.unpaired;
      _sessionToken = null;
      _userCode     = null;
      paired        = false;
      status        = ConnStatus.disconnected;
      obstacleHold  = false;
      _tareCompletedForSession = false;
      _luggageReadyMonitoringEnabled = false;
      _noLuggagePromptActive = false;
    });
    _log('[PAIR] Disconnected and session cleared');
  }

  void _reconnect() {
    if (_pairingState != _PairingState.paired || _sessionToken == null) {
      _startPairing();
      return;
    }
    // Already have a token — just re-subscribe to the stream to check status
    setState(() => status = ConnStatus.searching);
    _watchPairingSession(_sessionToken!);
    _log('[PAIR] Reconnecting with existing session...');
  }

  // Derive a stable 6-digit code from the UUID token
  String _tokenToUserCode(String token) {
    final stripped = token.replaceAll('-', '');
    final val = int.tryParse(stripped.substring(0, 8), radix: 16) ?? 0;
    return (val % 1000000).abs().toString().padLeft(6, '0');
  }

  String _generateToken() {
    final rand = math.Random.secure();
    String hex(int bytes) => List.generate(bytes, (_) => rand.nextInt(256))
        .map((b) => b.toRadixString(16).padLeft(2, '0'))
        .join();
    return '${hex(4)}-${hex(2)}-${hex(2)}-${hex(2)}-${hex(6)}';
  }

  final List<FloorPlan> _floors = const [
    FloorPlan(
      id: 'L1',
      name: 'Level 1',
      assetPath: 'assets/floors/ZACH_floor_1.png',
      widthMeters: 144.491,
      heightMeters: 162.552,
      pixelWidth: 2400,
      pixelHeight: 2700,
      hallSegments: [
        // ---- Level 1 hallway centerlines----
        HallSegment(Offset(92.414, 147.441), Offset(90.126, 63.575)),
        HallSegment(Offset(79.891, 134.677), Offset(92.173, 133.473)),
        HallSegment(Offset(92.173, 133.473), Offset(113.787, 133.292)),
        HallSegment(Offset(92.896, 142.805), Offset(113.546, 142.564)),
        HallSegment(Offset(91.270, 115.833), Offset(133.775, 116.074)),
        HallSegment(Offset(89.885, 70.319), Offset(127.272, 68.031)),
        HallSegment(Offset(127.272, 83.805), Offset(127.272, 64.539)),
      ],
      elevators: [
        ElevatorZone(
          id: 'L1_FRONT_ELEV',
          centerMeters: Offset(84.768, 129.801), // near your main vertical hallway
          radiusMeters: 5.0,
        ),
        ElevatorZone(
          id: 'L1_BACK_ELEV',
          centerMeters: Offset(99.157, 53.642), // near your main vertical hallway
          radiusMeters: 5.0,
        ),
      ],
    ),

    FloorPlan(
      id: 'L2',
      name: 'Level 2',
      assetPath: 'assets/floors/ZACH_floor_2.png',
      widthMeters: 157.909,
      heightMeters: 177.648,
      pixelWidth: 2400,
      pixelHeight: 2700,
      hallSegments: [
        // Level 2 hallways
        HallSegment(Offset(101.786, 74.349), Offset(104.023, 154.291)),
        HallSegment(Offset(104.023, 154.291), Offset(123.366, 154.027)),
        HallSegment(Offset(123.103, 154.027), Offset(123.103, 144.355)),
        HallSegment(Offset(104.286, 144.092), Offset(123.103, 144.355)),
        HallSegment(Offset(104.286, 144.092), Offset(89.811, 144.092)),
        HallSegment(Offset(102.048, 126.130), Offset(139.618, 126.130)),
        HallSegment(Offset(140.078, 72.836), Offset(139.618, 134.223)),
        HallSegment(Offset(139.618, 77.639), Offset(101.786, 77.376)),
        HallSegment(Offset(56.847, 101.983), Offset(101.786, 93.627)),
        HallSegment(Offset(56.847, 107.049), Offset(102.772, 111.392)),
        HallSegment(Offset(56.058, 72.309), Offset(57.571, 108.826)),
        HallSegment(Offset(50.991, 108.826), Offset(57.571, 108.826)),
        HallSegment(Offset(50.991, 108.826), Offset(51.518, 120.801)),
        HallSegment(Offset(43.359, 121.261), Offset(61.387, 120.801)),
        HallSegment(Offset(100.996, 61.124), Offset(101.522, 72.572)),
        HallSegment(Offset(100.996, 64.414), Offset(113.958, 64.940)),
        HallSegment(Offset(100.733, 52.768), Offset(122.840, 55.795)),
        HallSegment(Offset(100.733, 52.768), Offset(98.496, 16.186)),
        HallSegment(Offset(59.150, 30.661), Offset(98.693, 32.174)),
        HallSegment(Offset(82.244, 45.662), Offset(99.219, 45.925)),
        HallSegment(Offset(110.931, 58.361), Offset(111.194, 54.018)),
      ],
      elevators: [
        ElevatorZone(
          id: 'L2_FRONT_ELEV',
          centerMeters: Offset(95.403, 140.802), // near your main vertical hallway
          radiusMeters: 5.0,
        ),
        ElevatorZone(
          id: 'L2_BACK_ELEV',
          centerMeters: Offset(110.405, 61.914), // near your main vertical hallway
          radiusMeters: 5.0,
        ),
      ],
    ),

    FloorPlan(
      id: 'L3',
      name: 'Level 3',
      assetPath: 'assets/floors/ZACH_floor_3.png',
      widthMeters: 149.954,
      heightMeters: 168.699,
      pixelWidth: 2400,
      pixelHeight: 2700,
      hallSegments: [
        // Level 3 hallways
        HallSegment(Offset(88.910, 152.266), Offset(84.599, 16.120)),
        HallSegment(Offset(73.727, 143.144), Offset(88.910, 143.144)),
        HallSegment(Offset(125.337, 125.525), Offset(88.910, 125.774)),
        HallSegment(Offset(125.586, 85.037), Offset(125.586, 133.022)),
        HallSegment(Offset(133.272, 85.037), Offset(88.223, 84.787)),
        HallSegment(Offset(45.049, 105.781), Offset(89.160, 111.341)),
        HallSegment(Offset(45.049, 100.470), Offset(88.910, 92.784)),
        HallSegment(Offset(45.049, 100.470), Offset(43.362, 72.728)),
        HallSegment(Offset(52.984, 106.968), Offset(51.547, 120.963)),
        HallSegment(Offset(38.551, 119.526), Offset(51.547, 120.963)),
        HallSegment(Offset(38.551, 119.526), Offset(38.801, 105.531)),
        HallSegment(Offset(50.859, 106.468), Offset(38.801, 105.531)),
        HallSegment(Offset(45.047, 106.468), Offset(44.549, 95.909)),
        HallSegment(Offset(100.032, 65.043), Offset(87.473, 64.293)),
        HallSegment(Offset(74.227, 60.482), Offset(88.223, 61.169)),
      ],
      elevators: [
        ElevatorZone(
          id: 'L3_FRONT_ELEV',
          centerMeters: Offset(81.912, 140.020), // near your main vertical hallway
          radiusMeters: 5.0,
        ),
        ElevatorZone(
          id: 'L3_BACK_ELEV',
          centerMeters: Offset(96.408, 61.419), // near your main vertical hallway
          radiusMeters: 5.0,
        ),
      ],
    ),

    FloorPlan(
      id: 'L4',
      name: 'Level 4',
      assetPath: 'assets/floors/ZACH_floor_4.png',
      widthMeters: 149.142,
      heightMeters: 167.784,
      pixelWidth: 2400,
      pixelHeight: 2700,
      hallSegments: [
        // Level 4 hallways…
        HallSegment(Offset(72.540, 23.368), Offset(76.664, 143.144)),
        HallSegment(Offset(104.093, 119.964), Offset(75.664, 120.464)),
        HallSegment(Offset(32.303, 101.407), Offset(76.664, 106.030)),
        HallSegment(Offset(32.053, 95.409), Offset(76.164, 87.723)),
        HallSegment(Offset(31.553, 69.167), Offset(32.990, 101.219)),
        HallSegment(Offset(111.591, 83.600), Offset(75.414, 84.100)),
        HallSegment(Offset(86.536, 60.232), Offset(62.168, 55.421)),
      ],
      elevators: [
        ElevatorZone(
          id: 'L4_FRONT_ELEV',
          centerMeters: Offset(69.475, 133.481), // near your main vertical hallway
          radiusMeters: 5.0,
        ),
        ElevatorZone(
          id: 'L4_BACK_ELEV',
          centerMeters: Offset(83.644, 56.798), // near your main vertical hallway
          radiusMeters: 5.0,
        ),
      ],
    ),

    FloorPlan(
      id: 'L5',
      name: 'Level 5',
      assetPath: 'assets/floors/ZACH_floor_5.png',
      widthMeters: 144.644,
      heightMeters: 162.724,
      pixelWidth: 2400,
      pixelHeight: 2700,
      hallSegments: [
        // Level 5 hallways…
        HallSegment(Offset(88.775, 17.900), Offset(90.885, 121.561)),
        HallSegment(Offset(113.244, 115.052), Offset(90.222, 114.811)),
        HallSegment(Offset(100.407, 56.230), Offset(89.016, 55.989)),
        HallSegment(Offset(80.458, 52.494), Offset(90.222, 52.494)),
        HallSegment(Offset(89.257, 26.217), Offset(113.244, 25.795)),
        HallSegment(Offset(103.240, 37.848), Offset(103.481, 15.791)),
        HallSegment(Offset(88.112, 15.308), Offset(113.667, 15.790)),
      ],
      elevators: [
        ElevatorZone(
          id: 'L5_FRONT_ELEV',
          centerMeters: Offset(84.135, 129.456), // near your main vertical hallway
          radiusMeters: 5.0,
        ),
        ElevatorZone(
          id: 'L5_BACK_ELEV',
          centerMeters: Offset(98.358, 53.217), // near your main vertical hallway
          radiusMeters: 5.0,
        ),
      ],
    ),
  ];



  /// ------------------------
  /// UI
  /// ------------------------

  @override
  Widget build(BuildContext context) {
    final media = MediaQuery.of(context);

    // Pick base theme depending on highContrast
    final ThemeData appTheme = highContrast
        ? ThemeData(
            brightness: Brightness.dark,
            scaffoldBackgroundColor: Colors.black,
            colorScheme: const ColorScheme.dark(
              primary: Colors.white,
              secondary: Colors.white,
              surface: Colors.black,
              onSurface: Colors.white,
            ),
            // 👇 see section 2 for this CardThemeData fix
            cardTheme: CardThemeData(
              color: Colors.grey[900],
              elevation: 4,
            ),
            navigationBarTheme: NavigationBarThemeData(
              backgroundColor: Colors.black,
              indicatorColor: Colors.white24,
              labelTextStyle: WidgetStateProperty.all(
                const TextStyle(color: Colors.white),
              ),
              iconTheme: WidgetStateProperty.all(
                const IconThemeData(color: Colors.white),
              ),
            ),
            textTheme: const TextTheme(
              bodyLarge: TextStyle(color: Colors.white),
              bodyMedium: TextStyle(color: Colors.white),
              titleMedium: TextStyle(color: Colors.white),
            ),
            iconTheme: const IconThemeData(color: Colors.white),
          )
        : Theme.of(context);

    return MediaQuery(
      data: media.copyWith(
        textScaler: TextScaler.linear(textScale),
      ),
      child: Theme(
        data: appTheme,
        child: Scaffold(
          appBar: AppBar(
            title: const Text('Luggage Rover'),
            backgroundColor:
                highContrast ? Colors.black : appTheme.colorScheme.primary,
            foregroundColor:
                highContrast ? Colors.white : appTheme.colorScheme.onPrimary,
          ),
          body: [
            _buildConnectionTab(context), 
            _buildRoverControlSection(context),
            _buildLocationTab(context), 
            OutdoorCampusMapTab(
              followDistanceMeters: followDistanceMeters,
              userPose: _outdoorUserLatLng,
              roverPose: _outdoorRoverLatLng,
              onUserTap: _onOutdoorUserTap,
              gpsStatusText: _gpsDebugText.isNotEmpty
                ? '${_gpsStatusText()}\n\n$_gpsDebugText'
                : _gpsStatusText(),
            ),
            // _buildMapTab(context), 
            // IndoorMapTab(
            //   floors: _floors,
            //   poseStream: _poseStream,
            //   initialFloorId: 'L1',
            //   autoFloorDetect: autoFloorDetect,
            //   currentFloorId: _currentFloorId,
            //   onManualFloorPick: _onManualFloorPick,
            //   onStartPicked: _onMapStartPicked,
            //   followDistanceMeters: followDistanceMeters,
            //   showHallways: showHallways,
            //   obstaclesStream: obstaclesStream,
            //   onElevatorNear: _handleElevatorNear,
            // ),
            _buildSettingsTab(context),   // 3
            _buildLogTab(context),        // 4
            
          ][_tab],
          bottomNavigationBar: NavigationBar(
            selectedIndex: _tab,
            onDestinationSelected: (i) => setState(() => _tab = i),
            destinations: const [
              NavigationDestination(icon: Icon(Icons.wifi_tethering), label: 'Connect'),
              NavigationDestination(icon: Icon(Icons.directions_car), label: 'Controls'),
              NavigationDestination(icon: Icon(Icons.map), label: 'Rover'),
              NavigationDestination(icon: Icon(Icons.layers), label: 'Map'),
              NavigationDestination(icon: Icon(Icons.settings), label: 'Settings'),
              NavigationDestination(icon: Icon(Icons.list_alt), label: 'Log'),
            ],
          ),
        ),
      ),
    );
  }

  Widget _holdIconButton({
    required IconData icon,
    required Color bg,
    required Future<void> Function(int session) onHoldTick,
    Future<void> Function()? onRelease,
    bool enabled = true,
    String? disabledReason,
  }) {
    final child = Container(
      decoration: BoxDecoration(color: bg, shape: BoxShape.circle),
      padding: const EdgeInsets.all(12),
      child: Icon(icon),
    );

    void showDisabled() {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(disabledReason ?? 'Manual control disabled')),
      );
    }

    Future<void> handleRelease() async {
      await _stopHold(sendStop: false);
      if (onRelease != null) await onRelease();
    }

    // Use Listener (raw pointer) instead of GestureDetector so there is no
    // gesture-arena competition between tap and long-press recognizers.
    // Without this, Flutter fires onTapCancel at ~500ms (when long-press wins),
    // which was calling _stopHold and resetting the ramp mid-hold.
    return Opacity(
      opacity: enabled ? 1.0 : 0.35,
      child: Listener(
        behavior: HitTestBehavior.opaque,
        onPointerDown: enabled
            ? (_) => _startHold(onHoldTick)
            : (_) => showDisabled(),
        onPointerUp:     enabled ? (_) async { await handleRelease(); } : null,
        onPointerCancel: enabled ? (_) async { await handleRelease(); } : null,
        child: child,
      ),
    );
  }




  Widget _buildConnectionTab(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        // ── Step-by-step guidance banner ──────────────────────────────────
        _buildPairingGuidanceBanner(),
        const SizedBox(height: 20),

        // ── Main pairing card ─────────────────────────────────────────────
        switch (_pairingState) {
          _PairingState.unpaired    => _buildUnpairedCard(),
          _PairingState.inProgress  => _buildPairingInProgressCard(),
          _PairingState.paired      => _buildPairedCard(),
        },

        const SizedBox(height: 16),

        // ── Connection info ───────────────────────────────────────────────
        Card(
          child: ListTile(
            leading: Icon(
              _testOverride                                ? Icons.science       :
              _pairingState == _PairingState.inProgress   ? Icons.hourglass_top :
              _pairingState == _PairingState.unpaired     ? Icons.cancel        :
              status == ConnStatus.connected   ? Icons.check_circle :
              status == ConnStatus.searching   ? Icons.search       : Icons.cancel,
              color: _testOverride                                ? Colors.amber :
                     _pairingState == _PairingState.inProgress   ? Colors.amber :
                     _pairingState == _PairingState.unpaired     ? Colors.red   :
                     status == ConnStatus.connected   ? Colors.green :
                     status == ConnStatus.searching   ? Colors.amber : Colors.red,
            ),
            title: Text(
              _testOverride                              ? 'Connected (Test Mode)' :
              _pairingState == _PairingState.inProgress  ? 'Waiting…'             :
              _pairingState == _PairingState.unpaired    ? 'Disconnected'         :
              switch (status) {
                ConnStatus.connected    => 'Connected',
                ConnStatus.searching    => 'Searching…',
                ConnStatus.disconnected => 'Disconnected',
              }
            ),
            subtitle: Text(
              paired
                  ? switch (status) {
                      ConnStatus.connected =>
                        'Session active • Code: ${_userCode ?? '------'}',
                      ConnStatus.searching =>
                        'Reconnecting to rover… • Code: ${_userCode ?? '------'}',
                      ConnStatus.disconnected =>
                        'Rover offline • Session code: ${_userCode ?? '------'}',
                    }
                  : 'Not paired — tap Start Pairing above',
            ),
          ),
        ),

        const SizedBox(height: 8),
        SwitchListTile(
          title: const Text('Auto-reconnect'),
          subtitle: const Text('Automatically reconnect when signal is lost'),
          value: autoReconnect,
          onChanged: (v) => setState(() => autoReconnect = v),
        ),

        const SizedBox(height: 8),

        // ── Test override ─────────────────────────────────────────────────
        Card(
          color: _testOverride ? Colors.amber.shade50 : null,
          child: SwitchListTile(
            secondary: Icon(
              Icons.science,
              color: _testOverride ? Colors.amber.shade800 : Colors.grey,
            ),
            title: const Text('Test Override'),
            subtitle: Text(
              _testOverride
                  ? 'Connection & UWB checks bypassed — bench testing only'
                  : 'Bypass connection/UWB checks for manual drive or obstacle testing',
            ),
            value: _testOverride,
            onChanged: (v) {
              setState(() => _testOverride = v);
              _log(v ? '[TEST] Override ON — skipping connection guards' : '[TEST] Override OFF');
              if (v) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                    content: Text('⚠️ Test override ON — connection checks bypassed'),
                    backgroundColor: Colors.amber,
                    duration: Duration(seconds: 3),
                  ),
                );
              }
            },
          ),
        ),
        // After the "Test Override" card
        Card(
          color: _uwbOverrideEnabled ? Colors.orange.shade50 : null,
          child: SwitchListTile(
            secondary: Icon(
              Icons.location_off,
              color: _uwbOverrideEnabled ? Colors.orange.shade800 : Colors.grey,
            ),
            title: const Text('UWB Override'),
            subtitle: Text(
              _uwbOverrideEnabled
                  ? 'Driving allowed without UWB tracking'
                  : 'Require UWB tracking for manual control',
            ),
            value: _uwbOverrideEnabled,
            onChanged: (v) {
              setState(() => _uwbOverrideEnabled = v);
              _log(v ? '[UWB] Override ON — driving without UWB allowed' : '[UWB] Override OFF');

              _logValidationEvent(
                eventType: v ? 'uwb_override_on' : 'uwb_override_off',
              );

              _updateRoverCommand({'uwb_override_enabled': v});
              if (v) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                    content: Text('⚠️ UWB override ON — manual control available without tracking'),
                    backgroundColor: Colors.orange,
                    duration: Duration(seconds: 3),
                  ),
                );
              }
            },
          ),
        ),
      ],
    );
  }

  // ── Soft guidance banner (only shows when not paired) ─────────────────────
  Widget _buildPairingGuidanceBanner() {
    if (_pairingState == _PairingState.paired) return const SizedBox.shrink();

    final steps = _pairingState == _PairingState.inProgress
        ? [
            ('1', 'Make sure your rover is powered on and connected to Wi-Fi.'),
            ('2', 'Wait while the app and rover establish a secure session.'),
            ('3', 'Your session code will appear here once connected.'),
          ]
        : [
            ('1', 'Power on your rover and ensure it is connected to Wi-Fi.'),
            ('2', 'Tap Start Pairing below.'),
            ('3', 'Your session code will appear once the rover confirms.'),
          ];

    return Container(
      decoration: BoxDecoration(
        color: const Color(0xFFE3F2FD),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: const Color(0xFF90CAF9)),
      ),
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            const Icon(Icons.info_outline, color: Color(0xFF1565C0), size: 18),
            const SizedBox(width: 8),
            const Text('How to connect',
              style: TextStyle(fontWeight: FontWeight.w700,
                               color: Color(0xFF1565C0), fontSize: 13)),
          ]),
          const SizedBox(height: 10),
          ...steps.map((s) => Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 20, height: 20,
                  decoration: const BoxDecoration(
                    color: Color(0xFF1565C0),
                    shape: BoxShape.circle,
                  ),
                  alignment: Alignment.center,
                  child: Text(s.$1,
                    style: const TextStyle(color: Colors.white,
                                           fontSize: 11, fontWeight: FontWeight.bold)),
                ),
                const SizedBox(width: 10),
                Expanded(child: Text(s.$2,
                  style: const TextStyle(fontSize: 13, color: Color(0xFF1A237E)))),
              ],
            ),
          )),
        ],
      ),
    );
  }

  Widget _buildUnpairedCard() {
    return Card(
      elevation: 2,
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          children: [
            const Icon(Icons.lock_open, size: 48, color: Colors.grey),
            const SizedBox(height: 12),
            const Text('Not connected to rover',
              style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
            const SizedBox(height: 6),
            const Text('Tap the button below to begin pairing.',
              style: TextStyle(color: Colors.black54)),
            const SizedBox(height: 20),
            SizedBox(
              width: double.infinity,
              height: 48,
              child: FilledButton.icon(
                icon: const Icon(Icons.wifi_tethering),
                label: const Text('Start Pairing',
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
                onPressed: _startPairing,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildPairingInProgressCard() {
    return Card(
      elevation: 2,
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          children: [
            const SizedBox(
              width: 48, height: 48,
              child: CircularProgressIndicator(strokeWidth: 3),
            ),
            const SizedBox(height: 16),
            const Text('Pairing in progress…',
              style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
            const SizedBox(height: 6),
            const Text('Waiting for rover to confirm the session.',
              style: TextStyle(color: Colors.black54),
              textAlign: TextAlign.center),
            const SizedBox(height: 6),
            Text('Timeout in ${_pairingTimeout.inMinutes} minutes',
              style: const TextStyle(fontSize: 12, color: Colors.black38)),
            const SizedBox(height: 20),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton.icon(
                icon: const Icon(Icons.cancel_outlined),
                label: const Text('Cancel'),
                onPressed: () => _cancelPairing(reason: 'Pairing cancelled'),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildPairedCard() {
    return Card(
      elevation: 2,
      color: const Color(0xFFF1F8E9),
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          children: [
            const Icon(Icons.check_circle, size: 48, color: Color(0xFF2E7D32)),
            const SizedBox(height: 12),
            const Text('Pairing complete',
              style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600,
                               color: Color(0xFF2E7D32))),
            const SizedBox(height: 14),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(10),
                border: Border.all(color: const Color(0xFFA5D6A7), width: 1.5),
              ),
              child: Column(
                children: [
                  const Text('Your session code',
                    style: TextStyle(fontSize: 11, color: Colors.black45,
                                     letterSpacing: 0.5)),
                  const SizedBox(height: 4),
                  Text(_userCode ?? '------',
                    style: const TextStyle(
                      fontSize: 34,
                      fontWeight: FontWeight.w800,
                      letterSpacing: 8,
                      color: Color(0xFF1B5E20),
                    )),
                ],
              ),
            ),
            const SizedBox(height: 6),
            const Text('Keep this code — it identifies your current rover session.',
              style: TextStyle(fontSize: 11, color: Colors.black38),
              textAlign: TextAlign.center),
            const SizedBox(height: 20),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton.icon(
                    icon: const Icon(Icons.refresh),
                    label: const Text('Re-Pair'),
                    onPressed: _repairRover,
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: OutlinedButton.icon(
                    icon: const Icon(Icons.link_off),
                    label: const Text('Disconnect'),
                    style: OutlinedButton.styleFrom(foregroundColor: Colors.red),
                    onPressed: _disconnect,
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  

  /// 3.2.1.9.3 Start/Stop Tracking + 3.2.1.9.4 Alarm Notifications
  Widget _buildControlsTab(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(16),
      
    );
  }

  /// 3.2.1.9.5 Visual Location Display
  Widget _buildLocationTab(BuildContext context) {
    final uwb = _uwbPos;
    final bool uwbLive = uwb?.isLive ?? false;

    final overThreshold = distanceMeters > maxSeparationMeters;
    final pct = (distanceMeters / 10.0).clamp(0.0, 1.0);

    return Padding(
      padding: const EdgeInsets.all(16),
      child: ListView(
        children: [
          _RoverStatusBanner(
            obstacleHold: obstacleHold,
            obstacleAvoidActive: obstacleAvoidActive,
            obstacleReason: _lastObstacleReason,
            separationActive: separationActive,
            distanceMeters: distanceMeters,
            maxSeparationMeters: maxSeparationMeters,
            arrived: arrived,
            currentMode: _currentRoverMode,
            speedCmdAbs: speedCmdAbs,
          ),
          const SizedBox(height: 8),
          // ── Tracking switch (unchanged) ──────────────────────────
          SwitchListTile(
            title: const Text('Enable Tracking'),
            subtitle: const Text('Start or stop the rover following you'),
            value: tracking,
            onChanged: (v) {
              if (!_testOverride && status != ConnStatus.connected) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('Connect to the rover first')),
                );
                return;
              }
              setState(() => tracking = v);
              _log(v ? 'Tracking started' : 'Tracking stopped');
            },
          ),

          // ── Weight & status ──────────────────────────────────────────
          Card(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(
                      Icons.monitor_weight,
                      color: _luggageFallen && !_weightAlarmOverrideEnabled
                          ? Colors.red
                          : null,
                    ),
                    title: Text(
                      'Weight: ${displayUnit == WeightUnit.lb ? fmtLb(weightKg) : fmtKg(weightKg)}',
                      style: TextStyle(
                        color: weightKg <= _luggageWeightThresholdKg ? Colors.orange : null,
                        fontWeight: weightKg <= _luggageWeightThresholdKg
                            ? FontWeight.bold
                            : FontWeight.normal,
                      ),
                    ),
                    subtitle: Text(
                      _weightAlarmOverrideEnabled
                          ? 'Weight stop: OVERRIDDEN (rover can move)'
                          : weightKg <= _luggageWeightThresholdKg
                              ? '🧳 No luggage detected — please add luggage'
                              : _luggageFallen
                                  ? '⚠ LUGGAGE FELL OFF — rover stopped'
                                  : (obstacleHold
                                      ? 'Status: Obstacle Hold'
                                      : arrived
                                          ? 'Status: Arrived'
                                          : 'Status: Moving'),
                      style: TextStyle(
                        color: (weightKg <= _luggageWeightThresholdKg || (_luggageFallen && !_weightAlarmOverrideEnabled))
                            ? Colors.red
                            : null,
                        fontWeight:
                            (weightKg <= _luggageWeightThresholdKg || (_luggageFallen && !_weightAlarmOverrideEnabled))
                                ? FontWeight.bold
                                : FontWeight.normal,
                      ),
                    ),
                    trailing: Switch(
                      value: _weightAlarmOverrideEnabled,
                      onChanged: (v) {
                        setState(() => _weightAlarmOverrideEnabled = v);
                        _updateRoverCommand({'weight_alarm_override': v});
                        _log(v
                            ? '[WEIGHT] Override ON — weight stop bypassed on rover'
                            : '[WEIGHT] Override OFF — weight stop active on rover');

                        _logValidationEvent(
                          eventType: v ? 'weight_override_on' : 'weight_override_off',
                        );

                        ScaffoldMessenger.of(context).showSnackBar(
                          SnackBar(
                            content: Text(
                              v
                                  ? '⚠️ Weight override ON — rover ignores low-weight stop'
                                  : 'Weight override OFF — rover stops if weight drops',
                            ),
                            duration: const Duration(seconds: 2),
                          ),
                        );
                      },
                    ),
                  ),
                  const Divider(height: 16),
                  // ── Weight threshold slider ──────────────────────────
                  Text(
                    'Stop threshold: '
                    '${displayUnit == WeightUnit.lb ? fmtLb(_luggageWeightThresholdKg) : fmtKg(_luggageWeightThresholdKg)}'
                    '  (rover stops below this)',
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  ),
                  Slider(
                    min: 0.1,
                    max: 5.0,
                    divisions: 49,
                    label: displayUnit == WeightUnit.lb
                        ? fmtLb(_luggageWeightThresholdKg)
                        : fmtKg(_luggageWeightThresholdKg),
                    value: _luggageWeightThresholdKg.clamp(0.1, 5.0),
                    onChanged: (v) {
                      setState(() => _luggageWeightThresholdKg = v);
                    },
                    onChangeEnd: (v) {
                      // Only send to rover when user lifts finger
                      _updateRoverCommand({'luggage_weight_threshold_kg': v});
                      _log('[WEIGHT] Threshold set to ${fmtKg(v)}');
                    },
                  ),
                ],
              ),
            ),
          ),

          const SizedBox(height: 8),
          _buildSystemHealthCard(context),
          const SizedBox(height: 8),

          // ── UWB live badge ───────────────────────────────────────
          Card(
            color: uwbLive
                ? Colors.green.shade50
                : Colors.orange.shade50,
            child: ListTile(
              leading: Icon(
                uwbLive ? Icons.sensors : Icons.sensors_off,
                color: uwbLive ? Colors.green : Colors.orange,
              ),
              title: Text(
                uwbLive ? 'UWB Tracking: LIVE' : 'UWB Tracking: No signal',
                style: TextStyle(
                  fontWeight: FontWeight.bold,
                  color: uwbLive ? Colors.green.shade800 : Colors.orange.shade800,
                ),
              ),
              subtitle: uwb == null
                  ? const Text('Waiting for UWB publisher…')
                  : Text(
                      'Updated ${uwb.age.inSeconds}s ago  •  '
                      'Anchor 1: ${uwb.d1Meters.toStringAsFixed(3)} m  '
                      'Anchor 2: ${uwb.d2Meters.toStringAsFixed(3)} m',
                    ),
            ),
          ),

          const SizedBox(height: 8),

          // ── UWB position details ─────────────────────────────────
          if (uwb != null)
            Card(
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'UWB Position',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 24,
                      runSpacing: 6,
                      children: [
                        _UwbStat(label: 'X',        value: '${uwb.xMeters >= 0 ? '+' : ''}${uwb.xMeters.toStringAsFixed(3)} m'),
                        _UwbStat(label: 'Y',        value: '${uwb.yMeters.toStringAsFixed(3)} m'),
                        _UwbStat(label: 'Distance', value: '${uwb.distanceMeters.toStringAsFixed(3)} m  (${mToFt(uwb.distanceMeters).toStringAsFixed(1)} ft)'),
                        _UwbStat(label: 'Angle θ',  value: '${uwb.angleDeg >= 0 ? '+' : ''}${uwb.angleDeg.toStringAsFixed(1)}°'),
                      ],
                    ),
                    const SizedBox(height: 6),
                    Text(
                      'θ interpretation: ${_describeAngle(uwb.angleDeg)}',
                      style: const TextStyle(fontSize: 12, color: Colors.black54),
                    ),
                  ],
                ),
              ),
            ),

          const SizedBox(height: 8),

          // ── Separation alert banner (unchanged) ─────────────────
          if (separationActive)
            Card(
              color: Colors.red.shade50,
              child: ListTile(
                leading: const Icon(Icons.warning, color: Colors.red),
                title: const Text(
                  'Separation Alert',
                  style: TextStyle(fontWeight: FontWeight.bold),
                ),
                subtitle: Text(
                  'Rover is more than 6 ft away '
                  '(${mToFt(distanceMeters).toStringAsFixed(1)} ft). '
                  'Move closer to resume safe following.',
                ),
              ),
            ),

          

          // ── Follow distance picker (unchanged) ──────────────────
          Card(
            child: Padding(
              padding: const EdgeInsets.all(12.0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('Follow Distance', style: TextStyle(fontWeight: FontWeight.bold)),
                  const SizedBox(height: 8),
                  Text(
                    'Desired: ${mToFt(followDistanceMeters).toStringAsFixed(1)} ft '
                    '(${followDistanceMeters.toStringAsFixed(2)} m) • '
                    'Max separation: ${mToFt(maxSeparationMeters).toStringAsFixed(1)} ft',
                  ),
                  const SizedBox(height: 10),
                  LayoutBuilder(
                    builder: (context, c) {
                      return Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: _followFeetOptions.map((ft) {
                          final meters = ftToM(ft);
                          final selected = (followDistanceMeters - meters).abs() < 1e-6;
                          return ChoiceChip(
                            label: Text('${ft.toStringAsFixed(0)} ft'),
                            selected: selected,
                            onSelected: (_) async {
                              final targetCm = meters * 100.0;
                              setState(() => followDistanceMeters = meters);

                              if (_outdoorUserLatLng != null) {
                                _updateOutdoorUserPosition(_outdoorUserLatLng!);
                              }

                              // This is the ONLY place target_distance is written.
                              // The startup upsert intentionally omits it so this
                              // value is never clobbered on re-pair / hot-restart.
                              await _updateTargets(distance: targetCm);
                              _log('[FOLLOW] target_distance → '
                                  '${targetCm.toStringAsFixed(1)} cm '
                                  '(${ft.toStringAsFixed(0)} ft / '
                                  '${meters.toStringAsFixed(3)} m)');

                              if (mounted) {
                                ScaffoldMessenger.of(context).showSnackBar(
                                  SnackBar(
                                    content: Text(
                                      'Follow distance → ${ft.toStringAsFixed(0)} ft '
                                      '= ${targetCm.toStringAsFixed(0)} cm sent to rover',
                                    ),
                                    duration: const Duration(seconds: 3),
                                  ),
                                );
                              }
                            },
                          );
                        }).toList(),
                      );
                    },
                  ),
                ],
              ),
            ),
          ),

          // ── Distance visualization (reuses existing _LocationPainter) ──
          Card(
            elevation: 1,
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                children: [
                  Row(
                    children: [
                      const Icon(Icons.pin_drop),
                      const SizedBox(width: 8),
                      Text(
                        'Distance: ${distanceMeters.toStringAsFixed(2)} m '
                        '(${mToFt(distanceMeters).toStringAsFixed(1)} ft)',
                        style: const TextStyle(fontWeight: FontWeight.bold),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  LinearProgressIndicator(value: pct),
                  const SizedBox(height: 8),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: Text(
                      overThreshold
                          ? '⚠ Over 6 ft threshold'
                          : 'Within 6 ft threshold',
                      style: TextStyle(
                        color: overThreshold ? Colors.red : Colors.green,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                  Builder(
                    builder: (context) {
                      final screenWidth = MediaQuery.of(context).size.width;
                      final squareSize  = screenWidth * 0.8;
                      return SizedBox(
                        height: squareSize,
                        child: AspectRatio(
                          aspectRatio: 1.0,
                          child: Container(
                            decoration: BoxDecoration(
                              border: Border.all(color: Colors.black12),
                              borderRadius: BorderRadius.circular(12),
                            ),
                            child: CustomPaint(
                              painter: _LocationPainter(
                                distanceMeters,
                                maxSeparationMeters,
                                followDistanceMeters,
                                _uwbPos?.angleDeg ?? 0.0,
                              ),
                            ),
                          ),
                        ),
                      );
                    },
                  ),
                  const SizedBox(height: 8),
                  Wrap(
                    alignment: WrapAlignment.center,
                    spacing: 16,
                    runSpacing: 8,
                    children: const [
                      _LegendLineChip(
                        color: Color(0xFF1565C0),
                        label: 'Follow distance ring',
                      ),
                      _LegendLineChip(
                        color: Color(0xFFD32F2F),
                        label: '6 ft max separation ring',
                      ),
                      _LegendIconChip(
                        icon: Icons.person_pin_circle,
                        iconColor: Colors.blue,
                        label: 'You',
                      ),
                      _LegendIconChip(
                        icon: Icons.luggage,
                        iconColor: Colors.green,
                        label: 'Rover within range',
                      ),
                      _LegendIconChip(
                        icon: Icons.luggage,
                        iconColor: Colors.red,
                        label: 'Rover beyond 6 ft',
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),

          // NEW: Add Obstacle Sensor Overlay
          if (_obstacleData != null)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: ObstacleSensorOverlay(data: _obstacleData),
            ),
        ],
      ),
    );
  }

  Widget _buildMapTab(BuildContext context) {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.all(12),
          child: SegmentedButton<MapMode>(
            segments: const [
              ButtonSegment<MapMode>(
                value: MapMode.indoor,
                label: Text('ZACH Map'),
                icon: Icon(Icons.apartment),
              ),
              ButtonSegment<MapMode>(
                value: MapMode.outdoor,
                label: Text('Outdoor'),
                icon: Icon(Icons.park),
              ),
            ],
            selected: {_mapMode},
            onSelectionChanged: (s) {
              setState(() => _mapMode = s.first);
            },
          ),
        ),
        Expanded(
          child: _mapMode == MapMode.indoor
              ? IndoorMapTab(
                  floors: _floors,
                  poseStream: _poseStream,
                  initialFloorId: 'L1',
                  autoFloorDetect: autoFloorDetect,
                  currentFloorId: _currentFloorId,
                  onManualFloorPick: _onManualFloorPick,
                  onStartPicked: _onMapStartPicked,
                  followDistanceMeters: followDistanceMeters,
                  showHallways: showHallways,
                  obstaclesStream: obstaclesStream,
                  onElevatorNear: _handleElevatorNear,
                )
              : OutdoorCampusMapTab(
                  followDistanceMeters: followDistanceMeters,
                  userPose: _outdoorUserLatLng,
                  roverPose: _outdoorRoverLatLng,
                  onUserTap: _onOutdoorUserTap,
                  gpsStatusText: _gpsDebugText.isNotEmpty
                    ? '${_gpsStatusText()}\n\n$_gpsDebugText'
                    : _gpsStatusText(),
                ),
        ),
      ],
    );
  }


  /// Converts UWB angle to a human-readable description.
  String _describeAngle(double deg) {
    if (deg.abs() < 5) return 'directly ahead';
    if (deg > 0)       return '${deg.abs().toStringAsFixed(1)}° to your right';
    return '${deg.abs().toStringAsFixed(1)}° to your left';
  }

  /// 3.2.1.9.6 Event Log
  Widget _buildLogTab(BuildContext context) {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 6),
          child: Row(
            children: [
              const Text('Event Log', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
              const Spacer(),
              IconButton(
                tooltip: 'Clear log',
                onPressed: logs.isEmpty
                    ? null
                    : () => setState(() {
                          logs.clear();
                          _log('Log cleared');
                        }),
                icon: const Icon(Icons.delete_sweep),
              ),
            ],
          ),
        ),
        const Divider(height: 0),
        Expanded(
          child: logs.isEmpty
              ? const Center(child: Text('No events yet'))
              : ListView.separated(
                  itemCount: logs.length,
                  separatorBuilder: (context, index) => const Divider(height: 0),
                  itemBuilder: (context, i) {
                    final e = logs[i];
                    return ListTile(
                      leading: const Icon(Icons.bolt),
                      title: Text(e.message),
                      subtitle: Text(e.time.toLocal().toString()),
                    );
                  },
                ),
        ),
      ],
    );
  }

  Widget _buildSettingsTab(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(16),
      child: ListView(
        children: [
          const Text(
            'Settings & Accessibility',
            style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 16),

          // Text size
          ListTile(
            leading: const Icon(Icons.text_fields),
            title: const Text('Text size'),
            subtitle: Text(
              textScale == 1.0
                  ? 'Normal'
                  : textScale == 1.2
                      ? 'Large'
                      : 'Extra large',
            ),
            trailing: DropdownButton<double>(
              value: textScale,
              items: const [
                DropdownMenuItem(value: 1.0, child: Text('Normal')),
                DropdownMenuItem(value: 1.2, child: Text('Large')),
                DropdownMenuItem(value: 1.4, child: Text('Extra large')),
              ],
              onChanged: (v) {
                if (v == null) return;
                setState(() => textScale = v);
                _log('Text scale set to $v');
              },
            ),
          ),

          const SizedBox(height: 12),

          // High contrast
          SwitchListTile(
            secondary: const Icon(Icons.contrast),
            title: const Text('High contrast mode'),
            subtitle: const Text('Darker backgrounds and lighter text'),
            value: highContrast,
            onChanged: (v) {
              setState(() => highContrast = v);
              _log('High contrast mode: ${v ? "ON" : "OFF"}');
            },
          ),

          const SizedBox(height: 12),

          // Hallway overlay
          SwitchListTile(
            secondary: const Icon(Icons.route),
            title: const Text('Show hallway overlay'),
            subtitle: const Text('Show hallway paths and elevator zones on the map'),
            value: showHallways,
            onChanged: (v) {
              setState(() => showHallways = v);
              _log('Hallway overlay: ${v ? "ON" : "OFF"}');
            },
          ),

          // Units
          ListTile(
            leading: const Icon(Icons.scale),
            title: const Text('Units'),
            subtitle: Text(displayUnit == WeightUnit.lb ? 'Pounds (lb)' : 'Kilograms (kg)'),
            trailing: DropdownButton<WeightUnit>(
              value: displayUnit,
              items: const [
                DropdownMenuItem(value: WeightUnit.lb, child: Text('lb')),
                DropdownMenuItem(value: WeightUnit.kg, child: Text('kg')),
              ],
              onChanged: (u) {
                if (u == null) return;
                setState(() => displayUnit = u);
                _log('Units set to ${u == WeightUnit.lb ? "lb" : "kg"}');
              },
            ),
          ),

          const SizedBox(height: 24),
          const Divider(),
          const SizedBox(height: 8),

          const Text(
            'Rover behavior',
            style: TextStyle(fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 8),

          // Alarm notifications + auto floor detect (moved from Controls)
          SwitchListTile(
            title: const Text('Alarm Notifications'),
            subtitle: const Text('Receive alerts for 6 ft separation, weight changes, and obstacles'),
            value: notificationsEnabled,
            onChanged: (v) => setState(() => notificationsEnabled = v),
          ),
          SwitchListTile(
            title: const Text('Auto floor detect'),
            subtitle: const Text('Switch map floor based on incoming position data'),
            value: autoFloorDetect,
            onChanged: (v) {
              if (!mounted) return;

              setState(() {
                autoFloorDetect = v;
                _log('Auto floor detect: ${v ? "ON" : "OFF"}');
              });
            },
          ),

          const SizedBox(height: 16),
          const Divider(),
          const SizedBox(height: 8),

          const Text('Manual test triggers', style: TextStyle(fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),

          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: [
              OutlinedButton.icon(
                icon: const Icon(Icons.social_distance),
                label: const Text('Trigger 6ft Alert'),
                onPressed: () => _notify('Separation Alert', 'Distance exceeded 6 ft threshold.'),
              ),
              OutlinedButton.icon(
                icon: const Icon(Icons.work),
                label: const Text('Weight Change'),
                onPressed: () {
                  weightKg += lbToKg(0.5); // add 0.5 lb
                  _notify('Weight Change', 'Load is now ${fmtLb(weightKg)}.');
                },
              ),
              OutlinedButton.icon(
                icon: const Icon(Icons.warning),
                label: const Text('Obstacle Hold'),
                onPressed: () {
                  obstacleHold = true;
                  _notify('Obstacle Hold', 'Rover paused due to obstacle.');
                },
              ),
              OutlinedButton.icon(
                icon: const Icon(Icons.elevator),
                label: const Text('Test Floor Traverse'),
                onPressed: () {
                  // Pick some floor & elevator to simulate
                  final floor = _floors.firstWhere((f) => f.id == 'L2'); // for example
                  final zone = floor.elevators.isNotEmpty
                      ? floor.elevators.first
                      : ElevatorZone(id: 'TEST', centerMeters: const Offset(0, 0));
                  _handleElevatorNear(floor, zone);
                },
              ),

              OutlinedButton.icon(
                icon: const Icon(Icons.school),
                label: const Text('Start App Tutorial'),
                onPressed: () => _startTutorial(),
              ),

              OutlinedButton.icon(
                icon: const Icon(Icons.gps_fixed),
                label: const Text('Check GPS Permission'),
                onPressed: _debugGpsPermissionStatus,
              ),

              OutlinedButton.icon(
                icon: const Icon(Icons.settings_applications),
                label: const Text('Open App Settings'),
                onPressed: () async {
                  await Geolocator.openAppSettings();
                },
              ),

              OutlinedButton.icon(
                icon: const Icon(Icons.location_on),
                label: const Text('Open Location Settings'),
                onPressed: () async {
                  await Geolocator.openLocationSettings();
                },
              ),

              // OutlinedButton.icon(
              //   icon: const Icon(Icons.play_arrow),
              //   label: Text(_demoRunning ? 'Demo running…' : 'Run follow demo (no traffic)'),
              //   onPressed: _demoRunning ? null : _runClearHallwayDemo,
              // ),
              // OutlinedButton.icon(
              //   icon: const Icon(Icons.play_arrow),
              //   label: Text(_demoRunning ? 'Demo running…' : 'Run light-traffic demo'),
              //   onPressed: _demoRunning ? null : _runLightTrafficDemo,
              // ),
              OutlinedButton.icon(
                icon: const Icon(Icons.play_arrow),
                label: Text(_demoRunning ? 'Demo running…' : 'Run L1→L3 Demo (Supabase)'),
                onPressed: _demoRunning ? null : _runL1toL3SupabaseDemo,
              ),
              OutlinedButton.icon(
                icon: const Icon(Icons.stop),
                label: const Text('Stop Demo'),
                onPressed: _demoRunning ? _stopDemo : null,
              ),

              OutlinedButton.icon(
                icon: const Icon(Icons.pin_drop),
                label: const Text('Mark Ground-Truth Point'),
                onPressed: () {
                  _logValidationEvent(
                    eventType: 'ground_truth_mark',
                    groundTruthX: _uwbPos?.xMeters,
                    groundTruthY: _uwbPos?.yMeters,
                    notes: 'operator_mark',
                  );

                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('Ground-truth point logged')),
                  );
                },
              ),

              ElevatedButton(
                onPressed: _clearObstacleOverride,
                child: const Text("Clear Obstacle Override"),
              ),

            ],
          ),

          const SizedBox(height: 16),
          const Text('Validation Logging', style: TextStyle(fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),

          // ── Active-test banner (only shown while a test is running) ──────────
          if (_isTestRunning) ...[
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: Colors.green.shade50,
                borderRadius: BorderRadius.circular(10),
                border: Border.all(color: Colors.green.shade300, width: 1.5),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      const Icon(Icons.fiber_manual_record,
                          color: Colors.green, size: 14),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          'RECORDING: $_currentTestName',
                          style: const TextStyle(
                            fontWeight: FontWeight.bold,
                            color: Colors.green,
                            fontSize: 13,
                          ),
                        ),
                      ),
                      if (_testStartTime != null)
                        _TestElapsedTimer(startTime: _testStartTime!),
                    ],
                  ),
                  const SizedBox(height: 10),
                  if ((_testSteps[_currentTestName] ?? []).isNotEmpty) ...[
                    const Text('Steps:',
                        style: TextStyle(
                            fontWeight: FontWeight.bold, fontSize: 12)),
                    const SizedBox(height: 4),
                    ...(_testSteps[_currentTestName] ?? []).map(
                      (s) => Padding(
                        padding: const EdgeInsets.only(bottom: 3),
                        child: Text(s, style: const TextStyle(fontSize: 12)),
                      ),
                    ),
                    const SizedBox(height: 8),
                  ],
                  const Text('Pi terminal commands:',
                      style: TextStyle(
                          fontWeight: FontWeight.bold, fontSize: 12)),
                  const SizedBox(height: 4),
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(8),
                    decoration: BoxDecoration(
                      color: const Color(0xFF1E1E1E),
                      borderRadius: BorderRadius.circular(6),
                    ),
                    child: Text(
                      'python3 SUPA_uwb_host.py \\\n'
                      '  --test-name "${_currentTestName.replaceAll(' ', '_')}" \\\n'
                      '  --session-id "$_currentSessionId"\n\n'
                      'python3 NAVIAPP_movementtest.py \\\n'
                      '  --test-name "${_currentTestName.replaceAll(' ', '_')}" \\\n'
                      '  --session-id "$_currentSessionId"',
                      style: const TextStyle(
                        color: Colors.greenAccent,
                        fontFamily: 'monospace',
                        fontSize: 10,
                      ),
                    ),
                  ),
                  const SizedBox(height: 10),
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton.icon(
                      icon: const Icon(Icons.stop, size: 16),
                      label: const Text('Stop Test'),
                      style: FilledButton.styleFrom(
                          backgroundColor: Colors.red),
                      onPressed: () => _stopValidationTest(),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
          ],

          // ── Start buttons (hidden while a test is running) ───────────────────
          if (!_isTestRunning) ...[
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                OutlinedButton.icon(
                  icon: const Icon(Icons.play_arrow),
                  label: const Text('Start Distance Test'),
                  onPressed: () => _startValidationTest('Distance Control Step Response'),
                ),
                OutlinedButton.icon(
                  icon: const Icon(Icons.explore),
                  label: const Text('Start Heading Test'),
                  onPressed: () => _startValidationTest('Heading Control Step Response'),
                ),
                OutlinedButton.icon(
                  icon: const Icon(Icons.route),
                  label: const Text('Start Follow Test'),
                  onPressed: () => _startValidationTest('Follow-Me Mission Test'),
                ),
                OutlinedButton.icon(
                  icon: const Icon(Icons.wifi_off),
                  label: const Text('Start LOST_UWB Test'),
                  onPressed: () => _startValidationTest('LOST_UWB Failsafe'),
                ),
                OutlinedButton.icon(
                  icon: const Icon(Icons.pin_drop),
                  label: const Text('Start Tracking Accuracy'),
                  onPressed: () => _startValidationTest('Tracking Accuracy'),
                ),
                OutlinedButton.icon(
                  icon: const Icon(Icons.lock_outline),
                  label: const Text('Start Pairing Gate Test'),
                  onPressed: () => _startValidationTest('Secure Pairing Gate'),
                ),
                OutlinedButton.icon(
                  icon: const Icon(Icons.social_distance),
                  label: const Text('Start Bounds Safety Test'),
                  onPressed: () => _startValidationTest('Bounds Safety'),
                ),
                OutlinedButton.icon(
                  icon: const Icon(Icons.warning_amber),
                  label: const Text('Start Obstacle Test'),
                  onPressed: () => _startValidationTest('Obstacle Avoidance Override'),
                ),
                OutlinedButton.icon(
                  icon: const Icon(Icons.gamepad),
                  label: const Text('Start Manual Override Test'),
                  onPressed: () => _startValidationTest('Manual Override Mode'),
                ),
                OutlinedButton.icon(
                  icon: const Icon(Icons.monitor_weight),
                  label: const Text('Start Weight Alarm Test'),
                  onPressed: () => _startValidationTest('Weight Alarm Integration'),
                ),
                OutlinedButton.icon(
                  icon: const Icon(Icons.cell_tower),
                  label: const Text('Start Anchor Disruption Test'),
                  onPressed: () => _startValidationTest('Anchor Disruption'),
                ),
                OutlinedButton.icon(
                  icon: const Icon(Icons.timer_outlined),
                  label: const Text('Start Pipeline Latency Test'),
                  onPressed: () => _startValidationTest('UWB & Nav Pipeline Latency'),
                ),
              ],
            ),
            const SizedBox(height: 12),
          ] else ...[
            const Text(
              'Stop the current test before starting a new one.',
              style: TextStyle(color: Colors.grey, fontSize: 12),
            ),
            const SizedBox(height: 12),
          ],

          // Export button — always visible
          SizedBox(
            width: double.infinity,
            child: FilledButton.icon(
              icon: const Icon(Icons.download),
              label: Text(
                _isTestRunning
                    ? 'Export App Validation CSV (stop test first)'
                    : 'Export App Validation CSV'
                        ' (${_validationRows.length} rows)',
              ),
              style: FilledButton.styleFrom(
                backgroundColor: _isTestRunning ? Colors.grey : null,
              ),
              onPressed: _isTestRunning ? null : _exportValidationCsv,
            ),
          ),

          const SizedBox(height: 16),
          const Text('Operator Notes', style: TextStyle(fontWeight: FontWeight.bold)),

          const SizedBox(height: 8),

          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              OutlinedButton.icon(
                icon: const Icon(Icons.flag),
                label: const Text('Log Waypoint Reached'),
                onPressed: () => _logValidationNote('waypoint_reached'),
              ),
              OutlinedButton.icon(
                icon: const Icon(Icons.pause),
                label: const Text('Log Pause'),
                onPressed: () => _logValidationNote('user_pause'),
              ),
            ],
          ),

          const SizedBox(height: 24),

          const Text(
            'Coming soon',
            style: TextStyle(fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 8),
          const Text(
            '• Haptic feedback for alerts\n'
            '• Voice-friendly descriptions for screen readers\n'
            '• Custom color-blind themes',
            style: TextStyle(fontSize: 12, color: Colors.black54),
          ),

        ],
      ),
    );
  }

  /// ============================================================================
  /// NEW: System Health Dashboard Card
  /// ============================================================================

  Widget _buildSystemHealthCard(BuildContext context) {
    final hs = _healthStatus;

    // If we haven't received any health yet, show a placeholder card.
    if (hs == null) {
      return Card(
        child: ListTile(
          leading: const Icon(Icons.health_and_safety),
          title: const Text(
            'System Health',
            style: TextStyle(fontWeight: FontWeight.bold),
          ),
          subtitle: const Text('Waiting for diagnostics…'),
        ),
      );
    }

    final overall = hs.overallHealth;

    IconData icon;
    Color color;
    String label;

    switch (overall) {
      case SystemHealth.healthy:
        icon = Icons.check_circle;
        color = Colors.green;
        label = 'HEALTHY';
        break;
      case SystemHealth.degraded:
        icon = Icons.warning_amber_rounded;
        color = Colors.orange;
        label = 'DEGRADED';
        break;
      case SystemHealth.critical:
        icon = Icons.error;
        color = Colors.red;
        label = 'CRITICAL';
        break;
      case SystemHealth.offline:
        icon = Icons.cloud_off;
        color = Colors.grey;
        label = 'OFFLINE';
        break;
    }

    final ageText = hs.uwbAge == null
        ? 'n/a'
        : '${hs.uwbAge!.inMilliseconds} ms';


    return Card(
      color: color.withOpacity(0.06),
      child: ExpansionTile(
        leading: Icon(icon, color: color),
        title: Text(
          'System Health: $label',
          style: TextStyle(fontWeight: FontWeight.bold, color: color),
        ),
        subtitle: Text(
          'UWB: ${hs.uwbStatus ?? (hs.uwbLive ? "LIVE" : "OFFLINE")} • '
        ),
        childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        children: [

          const SizedBox(height: 12),

          // Quick status chips
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _healthChip(
                label: 'Paired',
                ok: hs.paired,
                okText: 'YES',
                badText: 'NO',
              ),
              _healthChip(
                label: 'UWB Live',
                ok: hs.uwbLive,
                okText: 'LIVE',
                badText: 'OFF',
              ),

              _sensorHealthChip('Front Sensor', _obstacleData?.frontCm, 61),
              _sensorHealthChip('Back Sensor', _obstacleData?.backCm, 61),
              _sensorHealthChip('Left Sensor', _obstacleData?.leftCm, 61),
              _sensorHealthChip('Right Sensor', _obstacleData?.rightCm, 61),
            ],
          ),

          const SizedBox(height: 12),

          Text(
            'Last update: ${hs.lastUpdate.toLocal()}',
            style: const TextStyle(fontSize: 12, color: Colors.black54),
          ),
        ],
      ),
    );
  }

  Widget _healthChip({
    required String label,
    required bool ok,
    required String okText,
    required String badText,
  }) {
    final bg = ok ? Colors.green.withOpacity(0.12) : Colors.red.withOpacity(0.12);
    final fg = ok ? Colors.green.shade800 : Colors.red.shade800;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: fg.withOpacity(0.3)),
      ),
      child: Text(
        '$label: ${ok ? okText : badText}',
        style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: fg),
      ),
    );
  }


  Widget _sensorHealthChip(String label, int? value, int threshold) {
    if (value == null) {
      return Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: Colors.grey.shade300,
          borderRadius: BorderRadius.circular(999),
          border: Border.all(color: Colors.grey.shade500),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.error_outline, size: 16, color: Colors.grey),
            const SizedBox(width: 6),
            Text(
              '$label: NO DATA',
              style: const TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w600,
                color: Colors.black87,
              ),
            ),
          ],
        ),
      );
    }

    final isNear = value < threshold;
    final fg = isNear ? Colors.red.shade800 : Colors.green.shade800;
    final bg = isNear
        ? Colors.red.withOpacity(0.10)
        : Colors.green.withOpacity(0.10);
    final border = isNear ? Colors.red : Colors.green;
    final icon = isNear ? Icons.warning : Icons.check_circle;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: border),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 16, color: fg),
          const SizedBox(width: 6),
          Text(
            '$label: $value cm',
            style: TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w600,
              color: fg,
            ),
          ),
        ],
      ),
    );
  }

  String _formatUptime(int seconds) {
    final hours = seconds ~/ 3600;
    final mins = (seconds % 3600) ~/ 60;
    if (hours > 0) return '${hours}h ${mins}m';
    return '${mins}m';
  }

  // Add these functions to your _RoverHomeState class in Flutter
  // These work with YOUR existing rover_commands schema

  // Helper to update command and increment version
  Future<void> _updateRoverCommand(Map<String, dynamic> updates) async {
    if (!mounted) return;

    final payload = <String, dynamic>{
      'robot_id': 'rover_01',
      ...updates,
      'command_updated_at': DateTime.now().toUtc().toIso8601String(),
    };

    if (_sessionToken != null) {
      payload['session_token'] = _sessionToken;
    }

    try {
      _log('[CMD] $payload');

      await supa
          .from('rover_commands')
          .upsert(payload, onConflict: 'robot_id');

      _log('[CMD] rover_commands upsert OK');
    } catch (e) {
      _log('[ERROR] Command failed: $e');
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Command failed: $e'),
            backgroundColor: Colors.red,
          ),
        );
      }
    }
  }

  // Emergency stop the rover
  Future<void> _sendEmergencyStop() async {
    _logValidationEvent(eventType: 'emergency_stop');
    await _updateRoverCommand({
      'emergency_stop': true,
      'mode': 'stop',
    });

    if (!mounted) return;

    setState(() {
      _currentRoverMode = 'stop'; // ADD THIS
    });
    _log('[COMMAND] Emergency stop sent');
    
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('🛑 Emergency stop activated')),
      );
    }
  }

  // Resume autonomous operation
  Future<void> _sendResume() async {
    _logValidationEvent(eventType: 'resume_auto');
    await _updateRoverCommand({
      'emergency_stop': false,
      'mode': 'auto',
    });

    if (!mounted) return;

    setState(() {
      _currentRoverMode = 'auto'; // ADD THIS
    });
    _log('[COMMAND] Resume autonomous mode');
    
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('▶️ Rover resumed')),
      );
    }
  }

  // Set rover mode
  Future<void> _setRoverMode(String mode) async {
    _log('[COMMAND] Setting mode to: $mode');
    _logValidationEvent(
      eventType: 'mode_change',
      notes: 'mode=$mode',
    );
    try {
      await _updateRoverCommand({
        'mode': mode,
        'emergency_stop': mode == 'stop',
        if (mode != 'manual') 'manual_left_speed': 0.0,
        if (mode != 'manual') 'manual_right_speed': 0.0,
      });

      if (!mounted) return;
      setState(() {
        _currentRoverMode = mode;
        if (mode != 'manual') {
          _currentTurnSpeed = 5.0;
          _currentStraightSpeed = 5.0;
        }
      });
      _log('[COMMAND] Mode set to $mode');
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('✅ Mode: $mode'), backgroundColor: Colors.green),
        );
      }
    } catch (e) {
      _log('[ERROR] Failed to set mode: $e');
    }
  }


  Future<void> _sendManualControl(double leftSpeed, double rightSpeed) async {
    final cappedLeft  = leftSpeed.clamp(-_maxSpeed, _maxSpeed);
    final cappedRight = rightSpeed.clamp(-_maxSpeed, _maxSpeed);

    await _updateRoverCommand({
      'mode': 'manual',
      'manual_override_mode': _manualOverrideModeEnabled,
      'manual_left_speed': cappedLeft,
      'manual_right_speed': cappedRight,
    });
  }


  // Update target distance/heading for autonomous mode
  Future<void> _updateTargets({double? distance, double? heading}) async {
    final updates = <String, dynamic>{};
    if (distance != null) updates['target_distance'] = distance;
    if (heading != null) updates['target_heading'] = heading;

    if (updates.isNotEmpty) {
      await _updateRoverCommand(updates);
      _log('[COMMAND] Updated targets: $updates');
    }
  }

  // Reset luggage sensor (tare only — zeros the current reading)
  Future<void> _resetLuggage() async {
    await _updateRoverCommand({
      'reset_luggage': true,
    });
    _log('[COMMAND] Luggage tare sent (re-zero baseline)');
    
    // Reset the flag after a moment
    Future.delayed(const Duration(seconds: 1), () {
      _updateRoverCommand({'reset_luggage': false});
    });
  }

  // Full recalibration: deletes saved cal file and restarts calibration sequence
  Future<void> _recalibrateLuggage() async {
    // Confirm with user since this is destructive
    final confirmed = await _showScaledDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Recalibrate Luggage Sensor?'),
        content: const Text(
          'This will delete the saved calibration and run full auto-calibration on the rover.\n\n'
          '1. Make sure the tray is EMPTY before confirming.\n'
          '2. After the rover zeros itself, place a known weight on the tray when prompted.\n\n'
          'The rover will be briefly unresponsive during calibration.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Recalibrate'),
          ),
        ],
      ),
    );

    if (confirmed != true) return;

    await _updateRoverCommand({
      'recalibrate_luggage': true,
    });
    _log('[COMMAND] Full luggage recalibration triggered — rover will delete cal file and restart calibration');

    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Recalibration started — keep tray empty, then place known weight when rover prompts'),
          duration: Duration(seconds: 6),
        ),
      );
    }

    // Reset the flag after a moment
    Future.delayed(const Duration(seconds: 2), () {
      _updateRoverCommand({'recalibrate_luggage': false});
    });
  }

  Future<void> _clearObstacleOverride() async {
    await _updateRoverCommand({'clear_obstacle_override': true});

    Future.delayed(const Duration(milliseconds: 400), () {
      _updateRoverCommand({'clear_obstacle_override': false});
    });
  }


  // ============================================================================
  // UI Widget for Rover Control Panel
  // ============================================================================

  Widget _buildRoverControlSection(BuildContext context) {
    return Column(
      children: [
        _RoverStatusBanner(
          obstacleHold: obstacleHold,
          obstacleAvoidActive: obstacleAvoidActive,
          obstacleReason: _lastObstacleReason,
          separationActive: separationActive,
          distanceMeters: distanceMeters,
          maxSeparationMeters: maxSeparationMeters,
          arrived: arrived,
          currentMode: _currentRoverMode,
          speedCmdAbs: speedCmdAbs,
        ),
        // NEW: Add System Health Dashboard at the top
        // Padding(
        //   padding: const EdgeInsets.all(16),
        //   child: _buildSystemHealthCard(),
        // ),
        
        Expanded(            // ← wrap the ListView in Expanded
          child: ListView(
            padding: const EdgeInsets.all(16),
            children: [
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        '🎮 Rover Control',
                        style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                      ),
                      const SizedBox(height: 12),

                      // ====== EMERGENCY STOP (BIG RED BUTTON) ======
                      SizedBox(
                        width: double.infinity,
                        height: 56,
                        child: FilledButton.icon(
                          style: FilledButton.styleFrom(
                            backgroundColor: Colors.red,
                            foregroundColor: Colors.white,
                          ),
                          onPressed: _sendEmergencyStop,
                          icon: const Icon(Icons.stop_circle, size: 28),
                          label: const Text(
                            'EMERGENCY STOP',
                            style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                          ),
                        ),
                      ),

                      const SizedBox(height: 12),

                      // ====== MODE SELECTION ======
                      const Text('Control Mode:',
                          style: TextStyle(fontWeight: FontWeight.w600)),
                      const SizedBox(height: 8),

                      SegmentedButton<String>(
                        segments: const [
                          ButtonSegment(value: 'auto', label: Text('Auto'), icon: Icon(Icons.autorenew)),
                          ButtonSegment(value: 'manual', label: Text('Manual'), icon: Icon(Icons.gamepad)),
                          ButtonSegment(value: 'stop', label: Text('Stop'), icon: Icon(Icons.stop)),
                        ],
                        selected: {_currentRoverMode},
                        onSelectionChanged: (Set<String> selected) {
                          _setRoverMode(selected.first);
                        },
                      ),

                      // After the SegmentedButton for mode selection
                      const SizedBox(height: 12),

                      // ====== OBSTACLE AVOIDANCE (works in AUTO + MANUAL) ======
                      Card(
                        color: _obstacleAvoidEnabled ? Colors.blue.shade50 : Colors.grey.shade100,
                        child: SwitchListTile(
                          secondary: Icon(
                            Icons.sensors,
                            color: _obstacleAvoidEnabled ? Colors.blue.shade800 : Colors.grey,
                          ),
                          title: const Text('Obstacle Avoidance'),
                          subtitle: Text(
                            _obstacleAvoidEnabled
                                ? 'Active in current mode — rover will turn/stop around obstacles'
                                : 'DISABLED — rover will NOT react to obstacles',
                          ),
                          value: _obstacleAvoidEnabled,
                          onChanged: (v) {
                            setState(() => _obstacleAvoidEnabled = v);
                            _setObstacleAvoidEnabled(v);
                            _log(v
                              ? '[OBSTACLE] Avoidance ON'
                              : '[OBSTACLE] Avoidance OFF — rover ignores obstacles');
                            _logValidationEvent(
                              eventType: v ? 'obstacle_avoidance_on' : 'obstacle_avoidance_off',
                            );
                          },
                        ),
                      ),

                      const SizedBox(height: 8),
                      Card(
                        color: _manualOverrideModeEnabled ? Colors.amber.shade50 : null,
                        child: SwitchListTile(
                          secondary: Icon(
                            Icons.warning_amber,
                            color: _manualOverrideModeEnabled ? Colors.amber.shade800 : Colors.grey,
                          ),
                          title: const Text('Manual Override Mode'),
                          subtitle: Text(
                            _manualOverrideModeEnabled
                                ? 'Hard-stops only in manual — avoidance turn logic OFF'
                                : 'Avoidance turn logic ACTIVE in manual mode',
                            style: const TextStyle(fontSize: 12),
                          ),
                          value: _manualOverrideModeEnabled,
                          onChanged: _currentRoverMode == 'manual' ? (v) {
                            setState(() => _manualOverrideModeEnabled = v);
                            _updateRoverCommand({'manual_override_mode': v});
                            _log(v
                              ? '[MANUAL] Override mode ON — avoidance disabled'
                              : '[MANUAL] Override mode OFF — turn logic active');
                            _logValidationEvent(
                              eventType: v ? 'manual_override_on' : 'manual_override_off',
                            );
                          } : null,
                        ),
                      ),

                      const SizedBox(height: 10),

                      // ====== SPEED CARD ======
                      Card(
                        elevation: 0,
                        child: Padding(
                          padding: const EdgeInsets.all(12),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Text('Current Speed (Commanded)',
                                  style: TextStyle(fontWeight: FontWeight.w600)),
                              const SizedBox(height: 6),
                              Text(
                                '${speedCmdAbs.toStringAsFixed(1)}%',
                                style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
                              ),
                              const SizedBox(height: 6),
                              LinearProgressIndicator(
                                value: (speedCmdAbs / 100.0).clamp(0.0, 1.0),
                              ),
                              const SizedBox(height: 8),
                              Text(
                                'Left: ${leftSpeedCmd.toStringAsFixed(1)}%  •  '
                                'Right: ${rightSpeedCmd.toStringAsFixed(1)}%  •  '
                                'Avg: ${speedCmdAvg.toStringAsFixed(1)}%',
                                style: const TextStyle(fontSize: 12, color: Colors.black54),
                              ),
                            ],
                          ),
                        ),
                      ),

                      const SizedBox(height: 16),
                      const Divider(),
                      const SizedBox(height: 12),

                      // ====== MANUAL CONTROLS ======
                      const Text('Manual Controls:',
                          style: TextStyle(fontWeight: FontWeight.w600)),
                      const SizedBox(height: 8),

                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                        children: [
                          _buildMotorControl(
                            label: 'Turn Left',
                            onForwardHoldTick: _sendTurnLeft,
                            onReverseHoldTick: _sendReverseTurnLeft,
                            onStopTap: _sendStop,
                          ),

                          _buildMotorControl(
                            label: 'Both',
                            onForwardHoldTick: _sendStraightForward,
                            onReverseHoldTick: _sendStraightReverse,
                            onStopTap: _sendStop,
                          ),

                          _buildMotorControl(
                            label: 'Turn Right',
                            onForwardHoldTick: _sendTurnRight,
                            onReverseHoldTick: _sendReverseTurnRight,
                            onStopTap: _sendStop,
                          ),

                        ],
                      ),

                      const SizedBox(height: 16),
                      const Divider(),
                      const SizedBox(height: 12),

                      // ====== AUTONOMOUS SETTINGS ======
                      const Text('Autonomous PID Targets:',
                          style: TextStyle(fontWeight: FontWeight.w600)),
                      const SizedBox(height: 4),
                      const Text(
                        'These reset the rover\'s internal PID controller targets. '
                        'Normally the follow distance (Rover tab) controls target distance automatically — '
                        'only use these if the rover\'s PID gets stuck.',
                        style: TextStyle(fontSize: 12, color: Colors.black54),
                      ),
                      const SizedBox(height: 8),

                      Row(
                        children: [
                          Expanded(
                            child: OutlinedButton.icon(
                              onPressed: () {
                                _updateTargets(distance: followDistanceMeters * 100.0);
                                _log('[PID] Distance target reset to current follow distance: '
                                    '${(followDistanceMeters * 100.0).toStringAsFixed(0)} cm');
                                ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                                  content: Text('PID distance target reset to follow distance'),
                                  duration: Duration(seconds: 2),
                                ));
                              },
                              icon: const Icon(Icons.straighten),
                              label: const Text('Reset PID Distance'),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: OutlinedButton.icon(
                              onPressed: () {
                                _updateTargets(heading: 0.0);
                                _log('[PID] Heading target reset to 0°');
                                ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                                  content: Text('PID heading reset to 0° (straight ahead)'),
                                  duration: Duration(seconds: 2),
                                ));
                              },
                              icon: const Icon(Icons.explore),
                              label: const Text('Reset PID Heading'),
                            ),
                          ),
                        ],
                      ),

                      const SizedBox(height: 8),

                      SizedBox(
                        width: double.infinity,
                        child: OutlinedButton.icon(
                          onPressed: _recalibrateLuggage,
                          icon: const Icon(Icons.scale),
                          label: const Text('Recalibrate Luggage Sensor'),
                        ),
                      ),
                      const Padding(
                        padding: EdgeInsets.only(top: 4, left: 4),
                        child: Text(
                          'Deletes the saved calibration and runs full auto-calibration on the rover. '
                          'Remove all luggage from the tray first, then place a known weight when prompted.',
                          style: TextStyle(fontSize: 11, color: Colors.black45),
                        ),
                      ),

                      const SizedBox(height: 12),

                      const Text('Quick Actions:',
                          style: TextStyle(fontWeight: FontWeight.w600)),
                      const SizedBox(height: 8),

                      SizedBox(
                        width: double.infinity,
                        child: FilledButton.icon(
                          onPressed: _sendResume,
                          icon: const Icon(Icons.play_arrow),
                          label: const Text('Resume Auto'),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }


  double _currentTurnSpeed = 0.0;
  double _currentStraightSpeed = 0.0;
  final double _speedIncrement = 5.0;
  final double _maxSpeed = 30.0;

  Future<void> _sendTurnLeft() async {
    if (!luggageOkForMotion) {
      _showNoLuggagePrompt();
      return;
    }

    if (_hardStopActive) { _showHardStopSnack(); return; }
    _currentTurnSpeed = (_currentTurnSpeed + _speedIncrement).clamp(0, _maxSpeed);
    await _sendManualControl(_currentTurnSpeed, -_currentTurnSpeed);

    if (!mounted) return;
    setState(() {
      leftSpeedCmd  = _currentTurnSpeed;
      rightSpeedCmd = -_currentTurnSpeed;
      speedCmdAvg   = 0;
      _speedAbsEma  = _currentTurnSpeed;
      speedCmdAbs   = _currentTurnSpeed;
    });
  }

  Future<void> _sendReverseTurnLeft() async {
    if (!luggageOkForMotion) {
      _showNoLuggagePrompt();
      return;
    }

    if (_hardStopActive) { _showHardStopSnack(); return; }
    _currentTurnSpeed = (_currentTurnSpeed + _speedIncrement).clamp(0, _maxSpeed);
    final l = -_currentTurnSpeed;
    final r = -(_currentTurnSpeed * 0.5);
    await _sendManualControl(l, r);

    if (!mounted) return;
    setState(() {
      leftSpeedCmd  = l;
      rightSpeedCmd = r;
      speedCmdAvg   = (l + r) / 2;
      _speedAbsEma  = (l.abs() + r.abs()) / 2;
      speedCmdAbs   = _speedAbsEma;
    });
  }

  Future<void> _sendTurnRight() async {
    if (!luggageOkForMotion) {
      _showNoLuggagePrompt();
      return;
    }

    if (_hardStopActive) { _showHardStopSnack(); return; }
    _currentTurnSpeed = (_currentTurnSpeed + _speedIncrement).clamp(0, _maxSpeed);
    await _sendManualControl(-_currentTurnSpeed, _currentTurnSpeed);

    if (!mounted) return;
    setState(() {
      leftSpeedCmd  = -_currentTurnSpeed;
      rightSpeedCmd = _currentTurnSpeed;
      speedCmdAvg   = 0;
      _speedAbsEma  = _currentTurnSpeed;
      speedCmdAbs   = _currentTurnSpeed;
    });
  }

  Future<void> _sendReverseTurnRight() async {
    if (!luggageOkForMotion) {
      _showNoLuggagePrompt();
      return;
    }

    if (_hardStopActive) { _showHardStopSnack(); return; }
    _currentTurnSpeed = (_currentTurnSpeed + _speedIncrement).clamp(0, _maxSpeed);
    final l = -(_currentTurnSpeed * 0.5);
    final r = -_currentTurnSpeed;
    await _sendManualControl(l, r);
    
    if (!mounted) return;
    setState(() {
      leftSpeedCmd  = l;
      rightSpeedCmd = r;
      speedCmdAvg   = (l + r) / 2;
      _speedAbsEma  = (l.abs() + r.abs()) / 2;
      speedCmdAbs   = _speedAbsEma;
    });
  }

  Future<void> _sendStraightForward() async {
    if (!luggageOkForMotion) {
      _showNoLuggagePrompt();
      return;
    }

    if (_hardStopActive) { _showHardStopSnack(); return; }
    _currentStraightSpeed = (_currentStraightSpeed + _speedIncrement).clamp(0, _maxSpeed);
    await _sendManualControl(_currentStraightSpeed, _currentStraightSpeed);

    if (!mounted) return;
    setState(() {
      leftSpeedCmd  = _currentStraightSpeed;
      rightSpeedCmd = _currentStraightSpeed;
      speedCmdAvg   = _currentStraightSpeed;
      _speedAbsEma  = _currentStraightSpeed;
      speedCmdAbs   = _currentStraightSpeed;
    });
  }

  Future<void> _sendStraightReverse() async {
    if (!luggageOkForMotion) {
      _showNoLuggagePrompt();
      return;
    }

    if (_hardStopActive) { _showHardStopSnack(); return; }
    _currentStraightSpeed = (_currentStraightSpeed + _speedIncrement).clamp(0, _maxSpeed);
    await _sendManualControl(-_currentStraightSpeed, -_currentStraightSpeed);
    
    if (!mounted) return;
    setState(() {
      leftSpeedCmd  = -_currentStraightSpeed;
      rightSpeedCmd = -_currentStraightSpeed;
      speedCmdAvg   = -_currentStraightSpeed;
      _speedAbsEma  =  _currentStraightSpeed;
      speedCmdAbs   =  _currentStraightSpeed;
    });
  }

  Future<void> _sendStop() async {

    final updates = <String, dynamic>{
      'manual_left_speed': 0.0,
      'manual_right_speed': 0.0,
    };

    if (_currentRoverMode == 'manual') {
      updates['mode'] = 'manual';
    }

    await _updateRoverCommand(updates);

    if (!mounted) return;
    setState(() {
      leftSpeedCmd = 0;
      rightSpeedCmd = 0;
      speedCmdAvg = 0;
      _speedAbsEma = 0;
      speedCmdAbs = 0;
    });
    _currentTurnSpeed = 0.0;
    _currentStraightSpeed = 0.0;
  }



  Widget _buildMotorControl({
    required String label,
    required Future<void> Function() onForwardHoldTick,
    required Future<void> Function() onReverseHoldTick,
    required Future<void> Function() onStopTap,
  }) {
    final reason = _manualBlockReason();
    final manualMotionEnabled = (reason == null);

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        _holdIconButton(
          icon: Icons.arrow_upward,
          bg: Colors.green.shade100,
          enabled: manualMotionEnabled,
          disabledReason: reason,
          onHoldTick: (session) async {
            if (_hardStopActive) {
              _showHardStopSnack();
              return;
            }
            if (!_testOverride && status != ConnStatus.connected) {
              _log('[UI] Ignoring manual motion: not connected');
              return;
            }
            if (session != _holdSession || !_isHolding) return;

            // ✅ Do NOT auto-switch modes.
            // Require the user to explicitly pick Manual mode.
            if (_currentRoverMode != 'manual') {
              _log('[UI] Ignoring manual motion: not in manual mode');
              if (mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('Switch to Manual mode to drive')),
                );
              }
              return;
            }

            await onForwardHoldTick();
          },
          onRelease: () => onStopTap(),
        ),

        const SizedBox(height: 4),
        Text(label, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
        const SizedBox(height: 4),

        // Stop should always work (safe)
        GestureDetector(
          onTap: () => unawaited(onStopTap()),
          child: Container(
            decoration: BoxDecoration(
              color: Colors.grey.shade200,
              shape: BoxShape.circle,
            ),
            padding: const EdgeInsets.all(12),
            child: const Icon(Icons.stop),
          ),
        ),

        const SizedBox(height: 4),

        _holdIconButton(
          icon: Icons.arrow_downward,
          bg: Colors.red.shade100,
          enabled: manualMotionEnabled,
          disabledReason: reason,
          onHoldTick: (session) async {
            if (_hardStopActive) {
              _showHardStopSnack();
              return;
            }
            if (!_testOverride && status != ConnStatus.connected) {
              _log('[UI] Ignoring manual motion: not connected');
              return;
            }
            if (session != _holdSession || !_isHolding) return;

            // ✅ Do NOT auto-switch modes.
            // Require the user to explicitly pick Manual mode.
            if (_currentRoverMode != 'manual') {
              _log('[UI] Ignoring manual motion: not in manual mode');
              if (mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('Switch to Manual mode to drive')),
                );
              }
              return;
            }

            await onReverseHoldTick();
          },
          onRelease: () => onStopTap(),
        ),
      ],
    );
  }


}


class _LegendIconChip extends StatelessWidget {
  final IconData icon;
  final Color iconColor;
  final String label;

  const _LegendIconChip({
    required this.icon,
    required this.iconColor,
    required this.label,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        border: Border.all(color: Colors.black12),
        borderRadius: BorderRadius.circular(999),
        color: Colors.white,
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, color: iconColor, size: 18),
          const SizedBox(width: 6),
          Text(
            label,
            style: const TextStyle(fontSize: 12),
          ),
        ],
      ),
    );
  }
}

class _LegendLineChip extends StatelessWidget {
  final Color color;
  final String label;

  const _LegendLineChip({
    required this.color,
    required this.label,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        border: Border.all(color: Colors.black12),
        borderRadius: BorderRadius.circular(999),
        color: Colors.white,
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 18,
            height: 18,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              border: Border.all(color: color, width: 2),
            ),
          ),
          const SizedBox(width: 6),
          Text(
            label,
            style: const TextStyle(fontSize: 12),
          ),
        ],
      ),
    );
  }
}

/// Painter for the simple “map” visualization
class _LocationPainter extends CustomPainter {
  final double distanceMeters;
  final double maxSeparationMeters;  // 6 ft ring
  final double followDistanceMeters; // chosen follow ring
  final double angleDeg;             // UWB angle: 0 = in front, +90 = right, -90 = left
  _LocationPainter(
    this.distanceMeters,
    this.maxSeparationMeters,
    this.followDistanceMeters, [
    this.angleDeg = 0.0,
  ]);

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width * 0.5, size.height * 0.5);
    final maxRadius = (size.shortestSide / 2) * 0.8;

    double mToPx(double m) {
      final t = (m / maxSeparationMeters).clamp(0.0, 1.0);
      return t * maxRadius;
    }

    final followPaintFill = Paint()
      ..color = const Color(0xFF1565C0).withValues(alpha: 0.2)
      ..style = PaintingStyle.fill;
    final followPaintStroke = Paint()
      ..color = const Color(0xFF1565C0)
      ..strokeWidth = 1.6
      ..style = PaintingStyle.stroke;

    final sepPaintFill = Paint()
      ..color = const Color(0xFFD32F2F).withValues(alpha: 0.2)
      ..style = PaintingStyle.fill;
    final sepPaintStroke = Paint()
      ..color = const Color(0xFFD32F2F)
      ..strokeWidth = 2.2
      ..style = PaintingStyle.stroke;

    final followR = mToPx(followDistanceMeters);
    canvas.drawCircle(center, followR, followPaintFill);
    canvas.drawCircle(center, followR, followPaintStroke);

    final sepR = mToPx(maxSeparationMeters);
    canvas.drawCircle(center, sepR, sepPaintFill);
    canvas.drawCircle(center, sepR, sepPaintStroke);

    final over = distanceMeters > maxSeparationMeters;
    final roverRadius = mToPx(distanceMeters);
    final angleRad = angleDeg * math.pi / 180.0;

    final rover = Offset(
      center.dx + roverRadius * math.sin(angleRad),
      center.dy - roverRadius * math.cos(angleRad),
    );

    final linePaint = Paint()
      ..color = Colors.black26
      ..strokeWidth = 2;

    canvas.drawLine(center, rover, linePaint);

    void drawMaterialIcon(
      Canvas canvas,
      Offset position,
      IconData icon,
      Color color, {
      double size = 28,
    }) {
      final textPainter = TextPainter(
        textDirection: TextDirection.ltr,
        text: TextSpan(
          text: String.fromCharCode(icon.codePoint),
          style: TextStyle(
            fontSize: size,
            fontFamily: icon.fontFamily,
            package: icon.fontPackage,
            color: color,
          ),
        ),
      )..layout();

      textPainter.paint(
        canvas,
        Offset(
          position.dx - textPainter.width / 2,
          position.dy - textPainter.height / 2,
        ),
      );
    }

    // Draw "You" exactly like legend: blue person icon
    drawMaterialIcon(
      canvas,
      center,
      Icons.person_pin_circle,
      Colors.blue,
      size: 30,
    );

    // Draw rover exactly like legend: luggage icon, green or red
    drawMaterialIcon(
      canvas,
      rover,
      Icons.luggage,
      over ? Colors.red : Colors.green,
      size: 26,
    );
  }



  @override
  bool shouldRepaint(covariant _LocationPainter old) =>
      old.distanceMeters != distanceMeters ||
      old.maxSeparationMeters != maxSeparationMeters ||
      old.followDistanceMeters != followDistanceMeters ||
      old.angleDeg != angleDeg;
}


// -----------------------------
// Indoor map models
// -----------------------------
class FloorPlan {
  final String id;
  final String name;
  final String assetPath;
  final double widthMeters;   // real-world width covered by the image
  final double heightMeters;  // real-world height covered by the image
  final int pixelWidth;       // image pixel width
  final int pixelHeight;      // image pixel height
  final List<HallSegment> hallSegments;
  final List<ElevatorZone> elevators;
  
  const FloorPlan({
    required this.id,
    required this.name,
    required this.assetPath,
    required this.widthMeters,
    required this.heightMeters,
    required this.pixelWidth,
    required this.pixelHeight,
    this.hallSegments = const [],
    this.elevators = const [], 
  });


  double get aspect => pixelWidth / pixelHeight;
}

class HallSegment {
  final Offset a; // start (meters in floor coordinates)
  final Offset b; // end   (meters in floor coordinates)
  const HallSegment(this.a, this.b);
}


class ElevatorZone {
  final String id;             // e.g. 'L1_MAIN_ELEV'
  final Offset centerMeters;   // center in floor coordinates (meters)
  final double radiusMeters;   // detection radius in meters

  const ElevatorZone({
    required this.id,
    required this.centerMeters,
    this.radiusMeters = 3.0,   // tweak as needed
  });
}



class UserPose {
  final String floorId;
  final double xMeters; // 0 at left edge of floor image
  final double yMeters; // 0 at top edge of floor image
  const UserPose({required this.floorId, required this.xMeters, required this.yMeters});
}

// -----------------------------
// Indoor map tab
// -----------------------------
class IndoorMapTab extends StatefulWidget {
  const IndoorMapTab({super.key, 
                      required this.floors, 
                      required this.poseStream, 
                      this.roverPoseStream,
                      this.initialFloorId,
                      this.currentFloorId,
                      this.autoFloorDetect = true,
                      this.onManualFloorPick,
                      this.onStartPicked,
                      this.followDistanceMeters = 0.6,
                      this.showHallways = true,
                      this.obstaclesStream,
                      this.onElevatorNear,
                      });
  final List<FloorPlan> floors;
  final Stream<UserPose> poseStream;      // hook your UWB/GPS stream here
  final Stream<RoverPose>? roverPoseStream;
  final String? initialFloorId;
  final bool autoFloorDetect;
  final ValueChanged<String>? onManualFloorPick;
  final void Function(UserPose pose)? onStartPicked;
  final double followDistanceMeters;
  final bool showHallways; 
  final Stream<ObstacleMarker>? obstaclesStream;
  final Future<void> Function(FloorPlan floor, ElevatorZone zone)? onElevatorNear;


  /// Floor that the parent (RoverHome) wants to show right now.
  /// When [autoFloorDetect] is true, this overrides the pose’s floorId.
  final String? currentFloorId;



  @override
  State<IndoorMapTab> createState() => _IndoorMapTabState();
}


class _IndoorMapTabState extends State<IndoorMapTab> {
  late String _floorId;

  

  // Last known user pose (persists on screen)
  UserPose? _lastPose;
  final Map<String, UserPose> _lastPoseByFloor = {};


  // Trail for rover lag
  final Map<String, List<Offset>> _trailByFloor = {};


  ElevatorZone? _lastElevatorZone;

  // Obstacle markers (optional)
  final List<ObstacleMarker> _obstacles = [];

  final Map<String, RoverPose> _lastRoverPoseByFloor = {};  
  StreamSubscription<RoverPose>? _roverPoseSub;

  @override
  void initState() {
    super.initState();
    _floorId = widget.initialFloorId ?? widget.floors.first.id;

    // still listen for obstacle markers if provided
    widget.obstaclesStream?.listen((m) {
      if (!mounted) return;
      setState(() {
        _obstacles.add(m);
        if (_obstacles.length > 20) _obstacles.removeAt(0);
      });
    });

    _roverPoseSub = widget.roverPoseStream?.listen((pose) {
      if (!mounted) return;
      setState(() {
        _lastRoverPoseByFloor[pose.floorId] = pose;
      });
    });
  }

  // --- trail helpers ---

  void _addToTrail(FloorPlan floor, Offset snappedMeters) {
    final trail = _trailByFloor.putIfAbsent(floor.id, () => <Offset>[]);

    if (trail.isEmpty) {
      trail.add(snappedMeters);
      return;
    }

    final last = trail.last;
    if ((snappedMeters - last).distance > 0.2) {
      trail.add(snappedMeters);
    }

    const maxPoints = 200;
    if (trail.length > maxPoints) {
      trail.removeRange(0, trail.length - maxPoints);
    }
  }


  Offset? _computeRoverPosition(String floorId, double followDistanceMeters) {
    final trail = _trailByFloor[floorId];
    if (trail == null || trail.isEmpty) return null;

    double remaining = followDistanceMeters;
    Offset cur = trail.last; // user is here

    for (int i = trail.length - 1; i > 0 && remaining > 0; i--) {
      final prev = trail[i - 1];
      final seg = cur - prev;
      final segLen = seg.distance;

      if (segLen <= 0) {
        cur = prev;
        continue;
      }

      if (segLen >= remaining) {
        final dir = seg / segLen;        // prev → cur
        return cur - dir * remaining;    // move back from user
      } else {
        remaining -= segLen;
        cur = prev;
      }
    }

    return trail.first;
  }


  @override
  Widget build(BuildContext context) {
    return StreamBuilder<UserPose>(
      stream: widget.poseStream,
      builder: (context, snapshot) {
        final pose = snapshot.data;

        // Update last known pose whenever a new one arrives
        if (pose != null) {
          _lastPose = pose;
          _lastPoseByFloor[pose.floorId] = pose; // ADD THIS
        }


        final effectivePose = _lastPose; // this is what we’ll use below

        // Decide which floor to show
        String activeFloorId = _floorId;

        if (widget.autoFloorDetect) {
          // Parent override wins
          if (widget.currentFloorId != null &&
              widget.floors.any((f) => f.id == widget.currentFloorId)) {
            activeFloorId = widget.currentFloorId!;
          }
          // Otherwise, follow the pose’s floor if we have one
          else if (effectivePose != null &&
              widget.floors.any((f) => f.id == effectivePose.floorId)) {
            activeFloorId = effectivePose.floorId;
          }
        }

        final floor = widget.floors.firstWhere(
          (f) => f.id == activeFloorId,
          orElse: () => widget.floors.first,
        );

        Offset? userMeters;
        Offset? roverMeters;

        // Use the most recent pose for THIS floor (not just the latest global pose)
        final poseForThisFloor = _lastPoseByFloor[floor.id];

        if (poseForThisFloor != null) {
          final rawMeters = Offset(
            poseForThisFloor.xMeters,
            poseForThisFloor.yMeters,
          );

          final snapped = clampToHallwaysMeters(rawMeters, floor);
          userMeters = snapped;

          _addToTrail(floor, snapped);
          
          final roverPoseForThisFloor = _lastRoverPoseByFloor[floor.id];

          if (roverPoseForThisFloor != null) {
            roverMeters = clampToHallwaysMeters(
              Offset(
                roverPoseForThisFloor.xMeters,
                roverPoseForThisFloor.yMeters,
              ),
              floor,
            );
          }

        }


        // Prune obstacles
        _obstacles.removeWhere(
          (m) => DateTime.now().difference(m.time).inSeconds > 10,
        );
        final markersForThisFloor = _obstacles
            .where((m) => m.floorId == floor.id)
            .map((m) => m.meters)
            .toList();

        // 🔽 IMPORTANT: still build the full UI even if pose == null
        return Column(
          children: [
            // Floor selector row
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 10, 12, 6),
              child: Row(
                children: [
                  const Icon(Icons.layers),
                  const SizedBox(width: 8),
                  Expanded(
                    child: DropdownButtonFormField<String>(
                      value: activeFloorId,
                      items: widget.floors
                          .map((f) => DropdownMenuItem(
                                value: f.id,
                                child: Text(f.name),
                              ))
                          .toList(),
                      onChanged: (v) {
                        if (v == null) return;
                        setState(() => _floorId = v);

                        // Tell parent so it stops forcing the old floor (like L3)
                        widget.onManualFloorPick?.call(v);
                      },
                      decoration: const InputDecoration(
                        labelText: 'Floor',
                        border: OutlineInputBorder(),
                        isDense: true,
                      ),
                    ),
                  ),
                ],
              ),
            ),

            // Tiny status line (optional)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12.0),
              child: Text(
                effectivePose == null
                    ? 'Long press on the map to mark your starting position.'
                    : 'Stream pose: floor ${effectivePose.floorId}, '
                      'x=${effectivePose.xMeters.toStringAsFixed(1)}, '
                      'y=${effectivePose.yMeters.toStringAsFixed(1)}',
                style: const TextStyle(fontSize: 11, color: Colors.black54),
              ),
            ),
            const SizedBox(height: 4),

            // Map view always shows
            Expanded(
              child: _IndoorMapView(
                floor: floor,
                userMeters: userMeters,
                roverMeters: roverMeters,
                showHallways: widget.showHallways,
                obstacleMeters: markersForThisFloor,
                onStartPicked: (p) {
                  if (!mounted) return;
                  setState(() {
                    _lastPose = p;

                    final f = widget.floors.firstWhere((x) => x.id == p.floorId);
                    final snapped = clampToHallwaysMeters(Offset(p.xMeters, p.yMeters), f);
                    _addToTrail(f, snapped);

                  });
                  widget.onStartPicked?.call(p); // this still writes to Supabase
                },
              ),
            ),
          ],
        );
      },
    );
  }

}




/// ============================================================================
/// NEW: Obstacle Sensor Overlay Widget
/// ============================================================================

class ObstacleSensorOverlay extends StatelessWidget {
  final ObstacleSensorData? data;
  final int thresholdCm;

  const ObstacleSensorOverlay({
    super.key,
    this.data,
    this.thresholdCm = 61,
  });

  @override
  Widget build(BuildContext context) {
    if (data == null) {
      return const SizedBox.shrink();
    }

    final screenWidth = MediaQuery.of(context).size.width;
    final painterSize = (screenWidth * 0.58).clamp(220.0, 300.0);
    final age = DateTime.now().difference(data!.timestamp);
    final isStale = age.inSeconds > 2;

    return Card(
      elevation: 2,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 22),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              'Obstacle Sensors',
              style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
            ),
            Text(
              isStale
                  ? 'No fresh sensor update (${age.inSeconds}s old)'
                  : 'Live sensor data',
              style: TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w600,
                color: isStale ? Colors.orange : Colors.green,
              ),
            ),
            const SizedBox(height: 24),
            // Offset the box slightly because all graphics are drawn on the top half now!
            SizedBox(
              width: painterSize,
              height: painterSize * 0.75, 
              child: CustomPaint(
                painter: _ObstacleSensorPainter(
                  front: data!.frontCm,
                  left: data!.leftCm,
                  right: data!.rightCm,
                  back: data!.backCm,
                  threshold: thresholdCm,
                ),
              ),
            ),
            const SizedBox(height: 16),
            
            // Renamed the chips to reflect the physical hardware layout
            Wrap(
              spacing: 8,
              runSpacing: 8,
              alignment: WrapAlignment.center,
              children: [
                _buildSensorChip('L-Fender', data!.leftCm, thresholdCm),
                _buildSensorChip('Center-L', data!.backCm, thresholdCm),
                _buildSensorChip('Center-R', data!.frontCm, thresholdCm),
                _buildSensorChip('R-Fender', data!.rightCm, thresholdCm),
              ],
            ),
            const SizedBox(height: 12),
            Text(
              'Updated: ${data!.timestamp.toLocal()}',
              style: const TextStyle(fontSize: 11, color: Colors.black54),
            ),
          ],
        ),
      ),
    );
  }
  
  Widget _buildSensorChip(String label, int? value, int threshold) {
    if (value == null) {
      return Chip(
        avatar: const Icon(Icons.error_outline, size: 16),
        label: Text('$label: --'),
        backgroundColor: Colors.grey.shade300,
      );
    }
    
    final isNear = value < threshold;
    final color = isNear ? Colors.red : Colors.green;
    
    return Chip(
      avatar: Icon(
        isNear ? Icons.warning : Icons.check_circle,
        size: 16,
        color: color,
      ),
      label: Text('$label: $value cm', style: const TextStyle(fontSize: 11)),
      backgroundColor: color.withOpacity(0.1),
      side: BorderSide(color: color, width: 1),
    );
  }
}

class _ObstacleSensorPainter extends CustomPainter {
  final int? front, left, right, back;
  final int threshold;
  
  const _ObstacleSensorPainter({
    this.front,
    this.left,
    this.right,
    this.back,
    required this.threshold,
  });
  
  @override
  void paint(Canvas canvas, Size size) {
    // Push the center down slightly since we are only drawing on the top half
    final center = Offset(size.width / 2, size.height * 0.75);
    final roverRadius = size.width * 0.15;
    
    // --- 1. Draw the Rover Body (Only happens once per frame now!) ---
    _drawRoverBody(canvas, center, roverRadius);
    
    // --- 2. Draw the Forward Shield Arcs ---
    // Straight ahead is -90 degrees (-math.pi / 2).
    final sweep = math.pi / 6; // 30 degrees
    
    // Left Fender (Angled 30 degrees left of center)
    _drawSensorArc(canvas, center, roverRadius, left, 'LF', -math.pi / 2 - (math.pi / 6), sweep, size);
    
    // Center-Left (The 'back' sensor, angled 10 degrees left of center)
    _drawSensorArc(canvas, center, roverRadius, back, 'CL', -math.pi / 2 - (math.pi / 18), sweep, size);
    
    // Center-Right (The 'front' sensor, angled 10 degrees right of center)
    _drawSensorArc(canvas, center, roverRadius, front, 'CR', -math.pi / 2 + (math.pi / 18), sweep, size);
    
    // Right Fender (Angled 30 degrees right of center)
    _drawSensorArc(canvas, center, roverRadius, right, 'RF', -math.pi / 2 + (math.pi / 6), sweep, size);
  }

  /// Helper function to draw the 4-wheel rectangular rover
  /// Helper function to draw the 4-wheel rectangular rover
  void _drawRoverBody(Canvas canvas, Offset center, double roverRadius) {
    // 1. Chassis Dimensions
    final chassisWidth = roverRadius * 1.3;
    final chassisHeight = roverRadius * 1.8;

    // 2. Draw the Black Wheels First (so they sit "under" the fenders)
    final wheelPaint = Paint()
      ..color = Colors.black87
      ..style = PaintingStyle.fill;
      
    final wheelWidth = roverRadius * 0.35;
    final wheelHeight = roverRadius * 0.65;
    
    // Push the wheels slightly outside the chassis
    final wheelXOffset = chassisWidth / 2 + (wheelWidth / 3); 
    final wheelYOffset = chassisHeight * 0.28;

    void drawWheel(double dx, double dy) {
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromCenter(center: Offset(center.dx + dx, center.dy + dy), width: wheelWidth, height: wheelHeight),
          const Radius.circular(4),
        ),
        wheelPaint,
      );
    }

    // Draw the 4 wheels
    drawWheel(-wheelXOffset, -wheelYOffset); // Front-Left
    drawWheel(-wheelXOffset, wheelYOffset);  // Back-Left
    drawWheel(wheelXOffset, -wheelYOffset);  // Front-Right
    drawWheel(wheelXOffset, wheelYOffset);   // Back-Right

    // 3. Draw the Cherry Red Fenders over the wheels
    final fenderPaint = Paint()
      ..color = Colors.red.shade700 // Cherry Red!
      ..style = PaintingStyle.fill;

    // Fenders are slightly wider and taller than the wheels, mounted flush to the chassis
    final fenderWidth = wheelWidth * 1.2;
    final fenderHeight = wheelHeight * 1.2;
    final fenderXOffset = chassisWidth / 2;

    void drawFender(double dx, double dy) {
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromCenter(center: Offset(center.dx + dx, center.dy + dy), width: fenderWidth, height: fenderHeight),
          const Radius.circular(5), // Slightly more rounded than the wheels
        ),
        fenderPaint,
      );
    }

    

    // 4. Draw the Rectangular Chassis
    final roverBodyPaint = Paint()
      ..color = Colors.black45 
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.0;

    final chassisRect = Rect.fromCenter(
      center: center, 
      width: chassisWidth, 
      height: chassisHeight
    );
    
    canvas.drawRRect(
      RRect.fromRectAndRadius(chassisRect, const Radius.circular(6)), 
      roverBodyPaint
    );

    // Draw the 4 fenders overlapping the wheels
    drawFender(-fenderXOffset, -wheelYOffset);
    drawFender(-fenderXOffset, wheelYOffset);
    drawFender(fenderXOffset, -wheelYOffset);
    drawFender(fenderXOffset, wheelYOffset);
    
    

    // 5. Draw Internal Longitudinal Lines (Rotated 90 degrees to run front-to-back)
    final internalLinePaint = Paint()
      ..color = Colors.black26 
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.5;
    
    // Left longitudinal divider
    canvas.drawLine(
      Offset(center.dx - chassisWidth * 0.20, center.dy - chassisHeight / 2),
      Offset(center.dx - chassisWidth * 0.20, center.dy + chassisHeight / 2),
      internalLinePaint
    );
    
    // Right longitudinal divider
    canvas.drawLine(
      Offset(center.dx + chassisWidth * 0.20, center.dy - chassisHeight / 2),
      Offset(center.dx + chassisWidth * 0.20, center.dy + chassisHeight / 2),
      internalLinePaint
    );
  }
  
  /// Helper function to draw the individual sensor sweeps
  void _drawSensorArc(
    Canvas canvas,
    Offset center,
    double roverRadius,
    int? distanceCm,
    String label,
    double angleRad,
    double sweepRad,
    Size size,
  ) {
    if (distanceCm == null) return;
    
    final isNear = distanceCm < threshold;
    final color = isNear ? Colors.red : Colors.green;
    
    // Scale distance to visual radius (max 300cm = edge of screen)
    final maxCm = 400.0;
    
    final normalizedDist = (distanceCm / maxCm).clamp(0.0, 1.0);
    // Start drawing the arc from the top edge of the rectangular chassis
    final arcRadius = (roverRadius * 0.9) + (size.width * 0.35 * normalizedDist);
    
    final arcRect = Rect.fromCircle(center: center, radius: arcRadius);
    
    // Draw filled arc
    final arcPaint = Paint()
      ..color = color.withOpacity(0.3)
      ..style = PaintingStyle.fill;
      
    canvas.drawArc(
      arcRect,
      angleRad - (sweepRad / 2),
      sweepRad,
      true,
      arcPaint,
    );
    
    // Draw arc border
    final borderPaint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2;
      
    canvas.drawArc(
      arcRect,
      angleRad - (sweepRad / 2),
      sweepRad,
      false,
      borderPaint,
    );
    
    // Draw distance label & ID slightly outside the arc
    final labelX = center.dx + math.cos(angleRad) * (arcRadius + 18);
    final labelY = center.dy + math.sin(angleRad) * (arcRadius + 18);
    
    final textPainter = TextPainter(
      text: TextSpan(
        text: '$label\n$distanceCm',
        style: TextStyle(
          color: color,
          fontWeight: FontWeight.bold,
          fontSize: 10,
        ),
      ),
      textAlign: TextAlign.center,
      textDirection: TextDirection.ltr,
    );
    textPainter.layout();
    textPainter.paint(
      canvas,
      Offset(labelX - textPainter.width / 2, labelY - textPainter.height / 2),
    );
  }
  
  @override
  bool shouldRepaint(_ObstacleSensorPainter old) =>
      old.front != front ||
      old.left != left ||
      old.right != right ||
      old.back != back;
}



// The pan/zoom map that overlays a user dot
class _IndoorMapView extends StatelessWidget {
  const _IndoorMapView({
    required this.floor,
    this.userMeters,
    this.roverMeters,
    this.showHallways = true,
    this.obstacleMeters = const [],
    this.onStartPicked,
  });

  final FloorPlan floor;
  final Offset? userMeters;   // user position (meters, snapped)
  final Offset? roverMeters;  // rover position (meters, along trail)
  final bool showHallways;
  final List<Offset> obstacleMeters; 
  final void Function(UserPose pose)? onStartPicked;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final maxW = constraints.maxWidth;
        final maxH = constraints.maxHeight;
        final imgAspect = floor.aspect;
 
        double viewW, viewH;
        if (maxW / maxH > imgAspect) {
          viewH = maxH;
          viewW = viewH * imgAspect;
        } else {
          viewW = maxW;
          viewH = viewW / imgAspect;
        }

        final List<Offset> obstaclePx = obstacleMeters.map((m) {
          final fx = (m.dx / floor.widthMeters).clamp(0.0, 1.0);
          final fy = (m.dy / floor.heightMeters).clamp(0.0, 1.0);
          return Offset(fx * viewW, fy * viewH);
        }).toList();

        // Convert meters → pixels for both dots
        Offset? userPx;
        if (userMeters != null) {
          final fx = (userMeters!.dx / floor.widthMeters).clamp(0.0, 1.0);
          final fy = (userMeters!.dy / floor.heightMeters).clamp(0.0, 1.0);
          userPx = Offset(fx * viewW, fy * viewH);
        }

        Offset? roverPx;
        if (roverMeters != null) {
          final fx = (roverMeters!.dx / floor.widthMeters).clamp(0.0, 1.0);
          final fy = (roverMeters!.dy / floor.heightMeters).clamp(0.0, 1.0);
          roverPx = Offset(fx * viewW, fy * viewH);
        }

        const markerSize = 18.0;

        return Center(
          child: ClipRRect(
            borderRadius: BorderRadius.circular(12),
            child: InteractiveViewer(
              minScale: 0.6,
              maxScale: 4,
              child: GestureDetector(
                behavior: HitTestBehavior.opaque,
                onLongPressStart: (details) {
                  if (onStartPicked == null) return;
                  final local = details.localPosition;

                  final fx = (local.dx / viewW).clamp(0.0, 1.0);
                  final fy = (local.dy / viewH).clamp(0.0, 1.0);
                  final xMeters = fx * floor.widthMeters;
                  final yMeters = fy * floor.heightMeters;

                  onStartPicked!(
                    UserPose(
                      floorId: floor.id,
                      xMeters: xMeters,
                      yMeters: yMeters,
                    ),
                  );
                },
                child: Stack(
                  children: [
                    // Floor image
                    SizedBox(
                      width: viewW,
                      height: viewH,
                      child: Image.asset(
                        floor.assetPath,
                        width: viewW,
                        height: viewH,
                        fit: BoxFit.fill,
                        filterQuality: FilterQuality.high,
                      ),
                    ),

                    // Hallways overlay — only when the toggle is ON
                    if (showHallways)
                      SizedBox(
                        width: viewW,
                        height: viewH,
                        child: CustomPaint(
                          painter: _HallwaysPainter(floor),
                        ),
                      ),
                    // Elevator boxes — now also controlled by showHallways
                    if (showHallways)
                      SizedBox(
                        width: viewW,
                        height: viewH,
                        child: CustomPaint(
                          painter: _ElevatorsPainter(floor),
                        ),
                      ),

                    // Rover marker (behind user)
                    if (roverPx != null)
                      Positioned(
                        left: roverPx.dx - markerSize / 2,
                        top: roverPx.dy - markerSize / 2,
                        child: const _RoverMarker(size: markerSize),
                      ),

                    // User marker
                    if (userPx != null)
                      Positioned(
                        left: userPx.dx - markerSize / 2,
                        top: userPx.dy - markerSize / 2,
                        child: const _UserMarker(size: markerSize),
                      ),

                    // Obstacle pins
                    for (final o in obstaclePx)
                      Positioned(
                        left: o.dx - 10,
                        top:  o.dy - 10,
                        child: const _ObstacleMarkerIcon(size: 20),
                      ),
                  ],
                ),
              ),
            ),
          ),
        );
      },
    );
  }
}


class _LegendChip extends StatelessWidget {
  const _LegendChip({
    required this.color,
    required this.label,
    this.filled = true,
  });

  final Color color;
  final String label;
  final bool filled;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 14,
          height: 14,
          decoration: BoxDecoration(
            color: filled ? color : Colors.transparent,
            shape: BoxShape.circle,
            border: Border.all(
              color: color,
              width: 2,
            ),
          ),
        ),
        const SizedBox(width: 6),
        Text(
          label,
          style: const TextStyle(fontSize: 11),
        ),
      ],
    );
  }
}



class _UwbStat extends StatelessWidget {
  const _UwbStat({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: const TextStyle(fontSize: 11, color: Colors.black54),
        ),
        Text(
          value,
          style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
        ),
      ],
    );
  }
}




class _UserMarker extends StatelessWidget {
  const _UserMarker({required this.size});
  final double size;
  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: Colors.blueAccent,
        shape: BoxShape.circle,
        border: Border.all(color: Colors.white, width: 2),
        boxShadow: const [BoxShadow(blurRadius: 6, color: Colors.black26)],
      ),
      child: const Icon(Icons.person_pin_circle, size: 14, color: Colors.white),
    );
  }
}

class _RoverMarker extends StatelessWidget {
  const _RoverMarker({required this.size});
  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: Colors.redAccent, // rover color
        shape: BoxShape.circle,
        border: Border.all(color: Colors.white, width: 2),
        boxShadow: const [BoxShadow(blurRadius: 6, color: Colors.black26)],
      ),
      child: const Icon(Icons.luggage, size: 14, color: Colors.white),
    );
  }
}

class _ObstacleMarkerIcon extends StatelessWidget {
  const _ObstacleMarkerIcon({required this.size});
  final double size;
  @override
  Widget build(BuildContext context) {
    return Container(
      width: size, height: size,
      decoration: BoxDecoration(
        color: Colors.orange,
        shape: BoxShape.circle,
        border: Border.all(color: Colors.white, width: 2),
        boxShadow: const [BoxShadow(blurRadius: 4, color: Colors.black26)],
      ),
      child: const Icon(Icons.warning_amber_rounded, size: 14, color: Colors.white),
    );
  }
}


class _RoverStatusBanner extends StatelessWidget {
  const _RoverStatusBanner({
    required this.obstacleHold,
    required this.obstacleAvoidActive,
    required this.obstacleReason,
    required this.separationActive,
    required this.distanceMeters,
    required this.maxSeparationMeters,
    required this.arrived,
    required this.currentMode,
    required this.speedCmdAbs,
  });

  final bool obstacleHold;
  final bool obstacleAvoidActive;
  final String? obstacleReason;
  final bool separationActive;
  final double distanceMeters;
  final double maxSeparationMeters;
  final bool arrived;
  final String currentMode;
  final double speedCmdAbs;

  @override
  Widget build(BuildContext context) {
    _BannerState state;
    String message;
    IconData icon;

    if (separationActive && obstacleHold) {
      state = _BannerState.critical;
      message = 'Too far (${mToFt(distanceMeters).toStringAsFixed(1)} ft) AND obstacle hold — rover stopped';
      icon = Icons.warning_amber_rounded;
    } else if (separationActive) {
      state = _BannerState.critical;
      message = 'Too far! ${mToFt(distanceMeters).toStringAsFixed(1)} ft away — rover stopped';
      icon = Icons.social_distance;
    } else if (obstacleHold) {
      state = _BannerState.warning;
      message = obstacleReason ?? 'Obstacle detected — rover stopped';
      icon = Icons.sensors;
    } else if (obstacleAvoidActive) {
      // Show the exact reason from Python e.g. "TURN LEFT: OBSTACLE RIGHT (45 cm)"
      state = _BannerState.warning;
      message = obstacleReason ?? 'Obstacle — avoidance manoeuvre active';
      icon = Icons.rotate_left;
    } else if (arrived) {
      state = _BannerState.success;
      message = 'Arrived at destination';
      icon = Icons.check_circle_outline;
    } else if (currentMode == 'stop') {
      state = _BannerState.critical;
      message = 'Emergency stop active';
      icon = Icons.stop_circle_outlined;
    } else if (currentMode == 'manual') {
      state = _BannerState.info;
      message = speedCmdAbs > 2
          ? 'Manual mode — moving at ${speedCmdAbs.toStringAsFixed(0)}%'
          : 'Manual mode — stopped';
      icon = Icons.gamepad_outlined;
    } else if (currentMode == 'auto') {
      state = _BannerState.ok;
      message = 'Following — ${mToFt(distanceMeters).toStringAsFixed(1)} ft away';
      icon = Icons.autorenew;
    } else {
      state = _BannerState.ok;
      message = 'System ready';
      icon = Icons.check_circle_outline;
    }

    final colors = _bannerColors(state);

    return AnimatedContainer(
      duration: const Duration(milliseconds: 300),
      curve: Curves.easeInOut,
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      decoration: BoxDecoration(
        color: colors.bg,
        border: Border(bottom: BorderSide(color: colors.border, width: 1.5)),
      ),
      child: Row(
        children: [
          // Pulsing icon for critical/warning states
          if (state == _BannerState.critical || state == _BannerState.warning)
            _PulsingIcon(icon: icon, color: colors.fg)
          else
            Icon(icon, color: colors.fg, size: 18),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              message,
              style: TextStyle(
                color: colors.fg,
                fontSize: 13,
                fontWeight: FontWeight.w600,
                letterSpacing: 0.1,
              ),
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          // Small mode badge
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
            decoration: BoxDecoration(
              color: colors.fg.withValues(alpha: 0.15),
              borderRadius: BorderRadius.circular(20),
            ),
            child: Text(
              currentMode.toUpperCase(),
              style: TextStyle(
                color: colors.fg,
                fontSize: 10,
                fontWeight: FontWeight.w800,
                letterSpacing: 0.8,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

enum _BannerState { ok, info, warning, critical, success }

class _BannerColors {
  final Color bg, fg, border;
  const _BannerColors({required this.bg, required this.fg, required this.border});
}

_BannerColors _bannerColors(_BannerState state) {
  return switch (state) {
    _BannerState.critical => _BannerColors(
        bg: const Color(0xFFFFEBEE),
        fg: const Color(0xFFC62828),
        border: const Color(0xFFEF9A9A),
      ),
    _BannerState.warning => _BannerColors(
        bg: const Color(0xFFFFF8E1),
        fg: const Color(0xFFE65100),
        border: const Color(0xFFFFCC80),
      ),
    _BannerState.success => _BannerColors(
        bg: const Color(0xFFE8F5E9),
        fg: const Color(0xFF2E7D32),
        border: const Color(0xFFA5D6A7),
      ),
    _BannerState.info => _BannerColors(
        bg: const Color(0xFFE3F2FD),
        fg: const Color(0xFF1565C0),
        border: const Color(0xFF90CAF9),
      ),
    _BannerState.ok => _BannerColors(
        bg: const Color(0xFFF1F8E9),
        fg: const Color(0xFF33691E),
        border: const Color(0xFFCCFF90),
      ),
  };
}

class _PulsingIcon extends StatefulWidget {
  const _PulsingIcon({required this.icon, required this.color});
  final IconData icon;
  final Color color;

  @override
  State<_PulsingIcon> createState() => _PulsingIconState();
}

class _PulsingIconState extends State<_PulsingIcon>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;
  late final Animation<double> _anim;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 800),
    )..repeat(reverse: true);
    _anim = Tween<double>(begin: 0.4, end: 1.0).animate(
      CurvedAnimation(parent: _ctrl, curve: Curves.easeInOut),
    );
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return FadeTransition(
      opacity: _anim,
      child: Icon(widget.icon, color: widget.color, size: 18),
    );
  }
}



class _HallwaysPainter extends CustomPainter {
  final FloorPlan floor;
  const _HallwaysPainter(this.floor);

  @override
  void paint(Canvas canvas, Size size) {
    if (floor.hallSegments.isEmpty) return;

    final paint = Paint()
      ..color = Colors.blueAccent.withValues(alpha: 0.2)  // faint
      ..strokeWidth = 4
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;

    for (final seg in floor.hallSegments) {
      // convert hallway endpoints from meters → pixels in this view
      final a = Offset(
        (seg.a.dx / floor.widthMeters) * size.width,
        (seg.a.dy / floor.heightMeters) * size.height,
      );
      final b = Offset(
        (seg.b.dx / floor.widthMeters) * size.width,
        (seg.b.dy / floor.heightMeters) * size.height,
      );
      canvas.drawLine(a, b, paint);
    }
  }


  @override
  bool shouldRepaint(covariant _HallwaysPainter oldDelegate) =>
      oldDelegate.floor != floor;
}


class _ElevatorsPainter extends CustomPainter {
  final FloorPlan floor;
  const _ElevatorsPainter(this.floor);

  @override
  void paint(Canvas canvas, Size size) {
    if (floor.elevators.isEmpty) return;

    final paint = Paint()
      ..color = Colors.purpleAccent.withOpacity(0.9)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 3;

    for (final z in floor.elevators) {
      final fx = z.centerMeters.dx / floor.widthMeters;
      final fy = z.centerMeters.dy / floor.heightMeters;
      final center = Offset(fx * size.width, fy * size.height);

      const half = 10.0;
      final rect = Rect.fromCenter(
        center: center,
        width: half * 2,
        height: half * 2,
      );
      canvas.drawRect(rect, paint);
    }
  }

  @override
  bool shouldRepaint(covariant _ElevatorsPainter oldDelegate) =>
      oldDelegate.floor != floor;
}






class UwbPosition {
  final double xMeters;
  final double yMeters;
  final double angleDeg;
  final double distanceMeters;
  final double d1Meters;
  final double d2Meters;
  final DateTime updatedAt;

  const UwbPosition({
    required this.xMeters,
    required this.yMeters,
    required this.angleDeg,
    required this.distanceMeters,
    required this.d1Meters,
    required this.d2Meters,
    required this.updatedAt,
  });

  factory UwbPosition.fromRow(Map<String, dynamic> row) {
    return UwbPosition(
      xMeters:       (row['x_m']        as num?)?.toDouble() ?? 0.0,
      yMeters:       (row['y_m']        as num?)?.toDouble() ?? 0.35,
      angleDeg:      (row['angle_deg']  as num?)?.toDouble() ?? 0.0,
      distanceMeters:(row['distance_m'] as num?)?.toDouble() ?? 0.35,
      d1Meters:      (row['d1_m']       as num?)?.toDouble() ?? 0.0,
      d2Meters:      (row['d2_m']       as num?)?.toDouble() ?? 0.0,
      updatedAt: DateTime.tryParse(row['updated_at'] as String? ?? '')
        ?? DateTime.fromMillisecondsSinceEpoch(0),
    );
  }

  // How stale this reading is
  Duration get age => DateTime.now().difference(updatedAt);
  bool get isLive => age.inMilliseconds < kUwbLiveThresholdMs;
}
// ============================================================================
// Elapsed timer widget — shows MM:SS since test started, updates every second
// ============================================================================
class _TestElapsedTimer extends StatefulWidget {
  const _TestElapsedTimer({required this.startTime});
  final DateTime startTime;

  @override
  State<_TestElapsedTimer> createState() => _TestElapsedTimerState();
}

class _TestElapsedTimerState extends State<_TestElapsedTimer> {
  late Timer _timer;
  late Duration _elapsed;

  @override
  void initState() {
    super.initState();
    _elapsed = DateTime.now().difference(widget.startTime);
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) {
        setState(() {
          _elapsed = DateTime.now().difference(widget.startTime);
        });
      }
    });
  }

  @override
  void dispose() {
    _timer.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final minutes = _elapsed.inMinutes.remainder(60).toString().padLeft(2, '0');
    final seconds = _elapsed.inSeconds.remainder(60).toString().padLeft(2, '0');
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: Colors.green.shade100,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        '$minutes:$seconds',
        style: const TextStyle(
          fontFamily: 'monospace',
          fontSize: 12,
          fontWeight: FontWeight.bold,
          color: Colors.green,
        ),
      ),
    );
  }
}
