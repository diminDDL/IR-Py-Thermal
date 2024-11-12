#!/usr/bin/env python3

import time
import irpythermal

# create camera object and do a initial calibration
cap = irpythermal.HT301()

# read frame (raw ADC data)
ret, frame = cap.read()

# read additional data associated with the last frame
info, temperature_lookup_table = cap.info()

point = (10, 20)
print(
    "temperature at point:",
    point,
    "is:",
    temperature_lookup_table[frame[point]],
    "°C",
    "[raw ADC value:",
    frame[point],
    "]",
)
# print('additional info:', info)

time.sleep(5)  # not needed
print("click, click")
cap.calibrate()
time.sleep(5)  # not needed

# read next frame
ret, frame = cap.read()
info, temperature_lookup_table = cap.info()

print(
    "temperature at point:",
    point,
    "is:",
    temperature_lookup_table[frame[point]],
    "°C",
    "[raw ADC value:",
    frame[point],
    "]",
)
# print('additional info:', info)

print("temperature at any point:", temperature_lookup_table[frame])
print("frame shape:", frame.shape, "data type:", frame.dtype)
# result: frame shape: (288, 384) data type: uint16
print(
    "T lut shape:",
    temperature_lookup_table.shape,
    "data type:",
    temperature_lookup_table.dtype,
)
# result: T lut shape: (16384,) data type: float64

# release camera object
cap.release()
