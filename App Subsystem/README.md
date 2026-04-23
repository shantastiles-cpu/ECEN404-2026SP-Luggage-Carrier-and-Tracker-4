# Rover Navigation App

A Flutter-based rover companion app for live navigation, pairing, telemetry monitoring, and validation testing. This app connects to rover data streams, displays positioning and health information, supports manual and autonomous interaction workflows, and exports validation logs for latency and navigation analysis.

## 🚀 Getting Started
To run this app locally, follow these steps:

### 1. Prerequisites
Install the Flutter SDK.

Install Android Studio or VS Code with the Flutter extension.

Ensure you have a physical device or emulator ready.

Create Supabase account to prepare SQL schema

### 2. Installation
Clone the repository:

Bash
git clone https://github.com/shantastiles-cpu/ECEN404-2026SP-Luggage-Carrier-and-Tracker-4.git
Navigate to the App Subsystem directory:

Bash
cd "App Subsystem"
Get the required packages:

Bash
flutter pub get

### 3. Running the App
Launch the app on your connected device using:

Bash
flutter run

### 4. Prepping Database

Copy database_schema.sql commands to the table editor in Supabase

Note: Be sure to enable realtime updates for the tables

## Features

- Rover pairing and reconnection workflow
- Live UWB navigation display
- GPS/map view for rover and user positioning
- Rover telemetry and system health monitoring
- Manual and autonomous mode support
- Realtime Supabase data updates
- Validation testing tools for:
  - UWB and navigation pipeline latency
  - App event logging
  - CSV export for offline analysis
- Visual UI cards for rover state, pairing, and diagnostics

## Tech Stack

- **Flutter**
- **Dart**
- **Supabase**
- **Realtime data subscriptions**
- **CSV export for validation logs**
