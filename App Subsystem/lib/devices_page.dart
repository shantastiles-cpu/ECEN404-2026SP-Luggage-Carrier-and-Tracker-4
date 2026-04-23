import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

// ngrok URL
const String baseUrl = 'https://unchecked-overhumbly-devyn.ngrok-free.dev';

class DeviceStatus {
  final String deviceId;
  final DateTime lastSeen;
  final Map<String, dynamic> values;

  DeviceStatus({
    required this.deviceId,
    required this.lastSeen,
    required this.values,
  });

  factory DeviceStatus.fromJson(Map<String, dynamic> json) {
    return DeviceStatus(
      deviceId: json['device_id'],
      lastSeen: DateTime.parse(json['last_seen']),
      values: Map<String, dynamic>.from(json['values']),
    );
  }
}

class DevicesPage extends StatefulWidget {
  const DevicesPage({super.key});

  @override
  State<DevicesPage> createState() => _DevicesPageState();
}

class _DevicesPageState extends State<DevicesPage> {
  late Future<List<DeviceStatus>> _futureDevices;

  @override
  void initState() {
    super.initState();
    _futureDevices = fetchDevices();
  }

  Future<List<DeviceStatus>> fetchDevices() async {
    final url = Uri.parse('$baseUrl/api/devices');
    final resp = await http.get(url);
    if (resp.statusCode != 200) {
      throw Exception('Failed to load devices: ${resp.statusCode}');
    }
    final List<dynamic> data = jsonDecode(resp.body);
    return data.map((e) => DeviceStatus.fromJson(e)).toList();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Raspberry Pi Devices')),
      body: RefreshIndicator(
        onRefresh: () async {
          setState(() {
            _futureDevices = fetchDevices();
          });
          await _futureDevices;
        },
        child: FutureBuilder<List<DeviceStatus>>(
          future: _futureDevices,
          builder: (context, snapshot) {
            if (snapshot.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snapshot.hasError) {
              return Center(child: Text('Error: ${snapshot.error}'));
            }

            final devices = snapshot.data ?? [];
            if (devices.isEmpty) {
              return const Center(child: Text('No devices found'));
            }

            return ListView.builder(
              itemCount: devices.length,
              itemBuilder: (context, index) {
                final d = devices[index];
                final v = d.values;
                return ListTile(
                  title: Text(d.deviceId),
                  subtitle: Text(
                    'Temp: ${v['temperature']}°C\n'
                    'Humidity: ${v['humidity']}%\n'
                    'Battery: ${v['battery']}V\n'
                    'Last seen: ${d.lastSeen}',
                  ),
                );
              },
            );
          },
        ),
      ),
    );
  }
}
