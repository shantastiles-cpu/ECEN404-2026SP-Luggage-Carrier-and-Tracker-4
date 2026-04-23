## Obstacle Avoidance and Weight Detection Subsystem

This subsystem covers the rover’s obstacle avoidance and weight detection functions.

It includes the ultrasonic sensing hardware and test code, the load cell scale and test code, and the final integrated navigation file where both systems are connected into the full rover operation.

🚀 Getting Started

To use this subsystem, review the included hardware and software files for ultrasonic sensing, load cell testing, and full-system integration.

### 1. Included Files

**four_sensors**
Code used to test the operation of the ultrasonic sensor array. These sensors are powered through a perf board and used to detect obstacles around the rover.

**read_weight**
Code used to test the operation of the load cell luggage scale and output weight readings in the terminal.

**NAVIAPP_MattUpdates4.py**
This is the main integration file where the obstacle avoidance and weight detection systems are combined into the rover’s full navigation and control system.

### 2. Hardware Notes

A large portion of this subsystem involved hardware design and integration.

This includes:

* Ultrasonic sensor array powered through a perf board
* Load cell and HX711 amplifier for luggage weight detection
* Buck converter PCB designed to step 24V down to 5V for Raspberry Pi power

A buck converter PCB was designed and tested for powering the Raspberry Pi from the rover battery system. The board was able to operate at 24V and supply 5A, but because the batteries could produce about 25V, the design was rated slightly too low and the PCB failed under that higher input. The design files and layout have still been included as part of this subsystem.

### 3. Integration

The ultrasonic sensing and weight detection systems are both integrated into the main rover navigation file:

**NAVIAPP_MattUpdates4.py**

This file is located in the home section of the repository and connects these safety systems with the rover’s navigation and control logic.

## Features

Obstacle detection using ultrasonic sensors
Perf board powered sensor array
Load cell luggage weight measurement
Terminal-based weight reading output
Integrated obstacle avoidance logic
Integrated luggage removal safety stop
Buck converter PCB design for Pi power
Combined operation in rover navigation code

## Tech Stack

Python
Raspberry Pi
Ultrasonic sensors
Load cell
HX711 amplifier
Perf board power distribution
Buck converter PCB design
