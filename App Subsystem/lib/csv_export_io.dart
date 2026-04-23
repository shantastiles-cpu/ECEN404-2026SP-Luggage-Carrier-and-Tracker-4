import 'dart:io';
import 'package:path_provider/path_provider.dart';

Future<void> exportCsvFile(String csvContent, {String fileName = 'naviapp_export.csv'}) async {
  final dir = await getApplicationDocumentsDirectory();
  final file = File('${dir.path}/$fileName');
  await file.writeAsString(csvContent);
}
