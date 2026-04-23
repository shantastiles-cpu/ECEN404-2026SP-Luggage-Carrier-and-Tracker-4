# Navigation Subsystem

**Texas A&M University | Electrical & Computer Engineering**

This repository contains the core **Navigation Control Subsystem** for the Luggage Rover 37 capstone project. Operating on a Raspberry Pi 5, this subsystem acts as the brain of the rover's drivetrain. It is fundamentally built around an advanced, mass-adaptive PID controller that translates raw spatial coordinates into smooth, stable motor commands for a differential-drive skid-steer chassis.

## 🚀 System Overview

The Luggage Rover operates with a highly variable payload (0 to 20+ lbs). A statically tuned PID would either respond too sluggishly when fully loaded or violently oscillate when empty. 

To solve this, our navigation core implements **Real-Time Gain Scheduling**. By continuously polling an integrated HX711 load cell, the software dynamically scales the proportional, integral, and derivative coefficients on the fly, ensuring mathematically optimal damping and rise times regardless of the luggage mass.

## ✨ Core PID Architecture

* **Dual-Axis Independent Loops:** The system utilizes two asynchronous PID controllers:
  * **Distance PID:** Governs forward/reverse linear acceleration to maintain a strict 1.0m follow buffer.
  * **Heading PID:** Implements 360-degree wrap-around logic to calculate the shortest rotational path to align the chassis with the user's UWB tag.
* **Real-Time Gain Scheduling:** Actively interpolates `Kp`, `Ki`, and `Kd` parameters based on live payload data, providing necessary inertial load compensation for the heavy Sabertooth-driven drivetrain.
* **Kinematic Slop Compensation:** Injects immediate dynamic power offsets directly to the PID output to break the static friction of heavy rubber tires before the integral loop is forced to "spool up" and cause overshoot.
* **Zero-Effort Deadbands:** Custom 5.0 cm and 5.0° mathematical tolerances immediately cut motor output to absolute zero upon reaching the target zone, eliminating drivetrain micro-jitter and preventing integral windup while resting.
* **Integral Anti-Windup:** Strict mathematical clamping on the integral accumulator prevents runaway oscillation when tracking targets at extended, out-of-bounds ranges.

## 🛠️ Hardware Interface

* **Compute:** Raspberry Pi 5 (Running Python 3)
* **Motor Driver:** Sabertooth 25x2 Dual Motor Controller (Serial at 115200 baud)
* **Telemetry Input:** X/Y tracking coordinates provided by a secondary UWB localization script.
