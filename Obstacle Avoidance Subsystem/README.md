This part of the project is the obstacle avoidance and weight detection.

The 2 pieces of code in here are testing the operation of the ultrasonic sensors which are powered by a perf board (four_sensors)
and the other code is to test the operation of the load cell weight scale, giving accurate readings in the terminal (read_weight).

The integration of both of these is within the file NAVIAPP_MattUpdates4.py with is located in the home page of this repository which can be found under the folders of each of the subsystems. 

A lot of this system was hardware. A Buck Converter PCB was designed as well, stepping 24V down to 5V to power a raspberry pi. Unfortunately it was rated a smidge low as the batteries would produce 25V, causing the PCB to blow however with the design and layout it was able to work at 24V pulling 5A, so that file has been added as well
