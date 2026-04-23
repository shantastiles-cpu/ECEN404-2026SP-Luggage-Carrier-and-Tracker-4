import 'dart:html' as html;

Future<void> exportCsvFile(String csvContent, {String fileName = 'naviapp_export.csv'}) async {
  final blob = html.Blob([csvContent], 'text/csv');
  final url = html.Url.createObjectUrlFromBlob(blob);
  final anchor = html.AnchorElement(href: url)
    ..setAttribute('download', fileName)
    ..click();
  html.Url.revokeObjectUrl(url);
}