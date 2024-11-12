#!/usr/bin/env python3

import argparse
import csv
import math
import sys
import threading
import time
from datetime import datetime

import cv2
import matplotlib.animation as animation
import matplotlib.patches as patches
import matplotlib.pyplot as plt
import numpy as np
import serial
from matplotlib.backend_bases import MouseButton
from mpl_toolkits.axes_grid1 import make_axes_locatable

import irpythermal
import utils

fps = 40
exposure = {
    "auto": True,
    "auto_type": "ends",  # 'center' or 'ends'
    "T_min": 0.0,
    "T_max": 50.0,
    "T_margin": 2.0,
}
draw_temp = True

# Argument parsing
parser = argparse.ArgumentParser(description="Thermal Camera Viewer")
parser.add_argument("-r", "--rawcam", action="store_true", help="use the raw camera")
parser.add_argument("-d", "--device", type=str, help="use the camera at camera_path")
parser.add_argument(
    "-o", "--offset", type=float, help="set a fixed offset for the temperature data"
)

# lock in thermometry options (all of these are requred)
parser.add_argument(
    "-l",
    "--lockin",
    type=float,
    help="enable lock-in thermometry with the given frequency (in Hz), ideally several times smaller than the camera fps",
)
parser.add_argument(
    "-p",
    "--port",
    type=str,
    help="set the serial port for the power supply control (will send 1 to turn on the load, 0 to turn it off new line terminated) at 115200 baud",
)
parser.add_argument(
    "-i",
    "--integration",
    type=float,
    help="set the integration time for the lock-in thermometry (in seconds)",
)


parser.add_argument(
    "file", nargs="?", type=str, help="use the emulator with the data in file.npy"
)
args = parser.parse_args()

# Choose the camera class
camera: irpythermal.Camera

lockin = False

if args.file and args.file.endswith(".npy"):
    camera = irpythermal.CameraEmulator(args.file)
else:
    camera_kwargs = {}
    if args.rawcam:
        camera_kwargs["camera_raw"] = True
    if args.device:
        camera_path = args.device
        cv2_cam = cv2.VideoCapture(camera_path)
        camera_kwargs["video_dev"] = cv2_cam
    if args.offset:
        camera_kwargs["fixed_offset"] = args.offset

    if args.lockin:
        lockin = True
        draw_temp = False
        # check if all lock-in thermometry options are provided
        if not args.port or not args.integration:
            print(
                "Error: lock-in thermometry also requires --port and --integration options"
            )
            sys.exit(1)

        fequency = args.lockin
        port = args.port
        integration = args.integration

    camera = irpythermal.Camera(**camera_kwargs)

# see https://matplotlib.org/tutorials/colors/colormaps.html
cmaps_idx = 1
cmaps = [
    "inferno",
    "coolwarm",
    "cividis",
    "jet",
    "nipy_spectral",
    "binary",
    "gray",
    "tab10",
]

# matplotlib.rcParams['toolbar'] = 'None'

# temporary fake frame
frame = np.full((camera.height, camera.width), 25.0)
quad_frame = np.full((camera.height, camera.width), 0.0)
in_phase_frame = np.full((camera.height, camera.width), 0.0)
lut = None  # will be defined later
start_skips = 2
is_capturing = False
lock = threading.Lock()
lock_in_thread = None


if lockin:
    fig, axes = plt.subplots(nrows=2, ncols=2, layout="tight")
    ax = axes[0][0]
    im = axes[0][0].imshow(frame, cmap=cmaps[cmaps_idx])
    im_in_phase = axes[0][1].imshow(frame, cmap=cmaps[cmaps_idx])
    im_quadrature = axes[1][1].imshow(frame, cmap=cmaps[cmaps_idx])
    axes[0][0].set_title("Live")
    axes[0][1].set_title("In-phase")
    axes[1][1].set_title("Quadrature")
    divider = make_axes_locatable(axes[0][0])
    divider_in_phase = make_axes_locatable(axes[0][1])
    divider_quadrature = make_axes_locatable(axes[1][1])
    cax = divider.append_axes("right", size="5%", pad=0.05)
    cax_in_phase = divider_in_phase.append_axes("right", size="5%", pad=0.05)
    cax_quadrature = divider_quadrature.append_axes("right", size="5%", pad=0.05)
    cbar = plt.colorbar(im, cax=cax)
    cbar_in_phase = plt.colorbar(im_in_phase, cax=cax_in_phase)
    cbar_quadrature = plt.colorbar(im_quadrature, cax=cax_quadrature)

    axes[1][0].axis("off")
    status_text = """
Frame: -,
Time: -/-,
Load: -,
Frequency: -Hz,
Integration Time: -s
Serial Port: -
"""
    status_text_obj = axes[1][0].text(
        0.05,
        0.95,
        status_text,
        verticalalignment="top",
        horizontalalignment="left",
        transform=axes[1][0].transAxes,
        fontsize=12,
        color="black",
    )

else:
    fig = plt.figure()
    ax = plt.gca()
    im = ax.imshow(frame, cmap=cmaps[cmaps_idx])
    divider = make_axes_locatable(ax)
    cax = divider.append_axes("right", size="5%", pad=0.05)
    cbar = plt.colorbar(im, cax=cax)

try:
    fig.canvas.set_window_title("Thermal Camera")
except:
    # does not work on windows
    pass

annotations = utils.Annotations(ax, patches)
temp_annotations = {
    "std": {"Tmin": "lightblue", "Tmax": "red", "Tcenter": "yellow"},
    "user": {},
}

# Add the patch to the Axes
roi = ((0, 0), (0, 0))


paused = False
update_colormap = True
diff = {"enabled": False, "annotation_enabled": False, "frame": np.zeros(frame.shape)}


csv_filename = None


def log_annotations_to_csv(annotation_frame):
    anns_data = []
    for type in ["std", "user"]:
        for ann_name in temp_annotations[type]:
            pos = annotations.get_pos(ann_name)
            val = round(annotations.get_val(ann_name, annotation_frame), 2)
            anns_data += [pos[0], pos[1], val]  # store each position and value
    if csv_filename is not None:
        with open(csv_filename, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([datetime.now()] + anns_data)


def get_lockin_frame(freq, port, integration):
    """This function will perform all of the lock-in thermometry operations, and return
    the in-phase and quadrature frames after the integration time is up, while controlling
    the load via serial communication based on the period of the signal."""

    global frame, is_capturing, status_text

    try:
        ser = serial.Serial(port, 115200)  # Open the serial port
    except serial.SerialException as exc:
        print(f"Error: could not open serial port {port} ({exc})")
        sys.exit(1)

    start_time = time.time()
    in_phase_sum = np.zeros((camera.height, camera.width))
    quadrature_sum = np.zeros((camera.height, camera.width))
    total_frames = 0

    # Calculate the period of the signal
    period = 1.0 / freq
    half_period = period / 2.0  # Toggle every half period
    load_on = True  # Track whether the load is on or off
    last_toggle_time = start_time  # Track the last time we toggled the load

    while (time.time() - start_time) < integration:
        current_time = time.time() - start_time  # Time since the loop started
        ret, raw_frame = camera.read()
        info, lut = camera.info()

        frame = camera.convert_to_frame(raw_frame, lut)

        if not ret:
            print("Error: could not read frame from camera")
            sys.exit(1)

        total_frames += 1

        # if(total_frames % 10 == 0):
        # print(f'Frame: {total_frames}, Time: {current_time:.2f}/{integration:.2f}')  # Debugging statement
        if True:
            status_text = f"""
Frame: {total_frames},
Time: {current_time:.2f}/{integration:.2f},
Load: {load_on},
Frequency: {freq:.2f}Hz,
Integration Time: {integration:.2f}s
Serial Port: {port}
"""

        # Check if a half period has passed (i.e., time to toggle the load)
        if current_time - (last_toggle_time - start_time) >= half_period:
            # Toggle the load state
            if load_on:
                ser.write(b"0\n")  # Send 0 to turn the load off
                load_on = False
            else:
                ser.write(b"1\n")  # Send 1 to turn the load on
                load_on = True

            # print(f'Load state: {load_on}, Time: {current_time}')  # Debugging statement

            # Update the last toggle time
            last_toggle_time += half_period

        # Calculate the phase angle based on the current time and frequency
        phase = 2 * math.pi * freq * current_time

        # Calculate sine and cosine factors
        sin_weight = 2 * math.sin(phase)
        cos_weight = -2 * math.cos(phase)

        # print(f"time: {current_time}, phase: {phase}, sin: {sin_weight}, cos: {cos_weight} , load: {load_on}")

        # Multiply the frame by sin and cos to get in-phase and quadrature components
        in_phase = raw_frame * sin_weight
        quadrature = raw_frame * cos_weight

        # Accumulate the sums
        in_phase_sum += in_phase
        quadrature_sum += quadrature

        if not is_capturing:
            break

    ser.write(b"0\n")
    ser.close()

    # After integration time, normalize by the total frames (optional)
    in_phase_sum /= total_frames
    quadrature_sum /= total_frames

    return in_phase_sum, quadrature_sum


def capture_lock_in():
    global is_capturing, quad_frame, in_phase_frame, lock, fequency, port, integration

    while is_capturing:
        in_phase, quad = get_lockin_frame(fequency, port, integration)
        if in_phase is not None and quad is not None:
            with lock:
                in_phase_frame = in_phase
                quad_frame = quad


def start_capture():
    global is_capturing
    is_capturing = True
    thread = threading.Thread(target=capture_lock_in)
    thread.start()
    return thread


def stop_capture(thread):
    global is_capturing
    is_capturing = False
    if thread is not None:
        thread.join()


def animate_func(i):
    global \
        frame, \
        in_phase_frame, \
        quad_frame, \
        paused, \
        update_colormap, \
        exposure, \
        im, \
        diff, \
        start_skips, \
        lock_in_thread
    if lockin and start_skips > 0:
        frame = camera.get_frame()
        start_skips -= 1
    elif lockin:
        if not is_capturing:
            lock_in_thread = start_capture()
        # in_phase_frame, quad_frame = get_lockin_frame(fequency, port, integration)
    else:
        frame = camera.get_frame()

    if not paused:
        if diff["enabled"]:
            show_frame = frame - diff["frame"]
        else:
            show_frame = frame

        if diff["annotation_enabled"]:
            annotation_frame = frame - diff["frame"]
        else:
            annotation_frame = frame

        im.set_array(show_frame)
        if lockin:
            im_in_phase.set_array(in_phase_frame)
            im_quadrature.set_array(quad_frame)

        annotations.update(temp_annotations, annotation_frame, draw_temp)

        if exposure["auto"]:
            update_colormap = utils.autoExposure(update_colormap, exposure, show_frame)

        # TODO deal with saving the lock in stuff to a file
        log_annotations_to_csv(annotation_frame)

        if update_colormap:
            im.set_clim(exposure["T_min"], exposure["T_max"])
            fig.canvas.draw_idle()  # force update all, even with blit=True
            update_colormap = False
            return []

        if lockin:
            # adjust the color limits for the in-phase and quadrature frames
            im_in_phase.set_clim(np.min(in_phase_frame), np.max(in_phase_frame))
            im_quadrature.set_clim(np.min(quad_frame), np.max(quad_frame))
            new_status_text = status_text
            status_text_obj.set_text(new_status_text)
            return [im, im_in_phase, im_quadrature, status_text_obj] + annotations.get()

    return [im] + annotations.get()


def print_help():
    print("""keys:
    'h'      - help
    'q'      - quit
    ' '      - pause, resume
    'd'      - set diff
    'x','c'  - enable/disable diff, enable/disable annotation diff
    'f'      - full screen
    'u'      - calibrate
    't'      - draw min, max, center temperature
    'e'      - remove user temperature annotations
    'w'      - save to file date.png
    'r'      - save raw data to file date.npy
    'v'      - record annotations data to file date.csv
    ',', '.' - change color map
    'a', 'z' - auto exposure on/off, auto exposure type
    'k', 'l' - set the thermal range to normal/high (supported by T2S+/T2L)
    left, right, up, down - set exposure limits

mouse:
    left  button - add Region Of Interest (ROI)
    right button - add user temperature annotation
""")


FILE_NAME_FORMAT = "%Y-%m-%d_%H-%M-%S"


# keyboard
def press(event):
    global \
        paused, \
        exposure, \
        update_colormap, \
        cmaps_idx, \
        draw_temp, \
        temp_extra_annotations, \
        csv_filename
    global lut, frame, diff, annotations, roi
    if event.key == "h":
        print_help()
    if event.key == " ":
        paused ^= True
        print("paused:", paused)
    if event.key == "d":
        diff["frame"] = frame
        diff["annotation_enabled"] = diff["enabled"] = True
        print("set   diff")
    if event.key == "x":
        diff["enabled"] ^= True
        print("enable diff:", diff["enabled"])
    if event.key == "c":
        diff["annotation_enabled"] ^= True
        print("enable annotation diff:", diff["annotation_enabled"])
    if event.key == "t":
        draw_temp ^= True
        print("draw temp:", draw_temp)
    if event.key == "e":
        print("removing user annotations: ", len(temp_annotations["user"]))
        annotations.remove(temp_annotations["user"])
    if event.key == "u":
        print("calibrate")
        camera.calibrate()
    if event.key == "a":
        exposure["auto"] ^= True
        print("auto exposure:", exposure["auto"], ", type:", exposure["auto_type"])
    if event.key == "z":
        types = ["center", "ends"]
        exposure["auto_type"] = types[types.index(exposure["auto_type"]) - 1]
        print("auto exposure:", exposure["auto"], ", type:", exposure["auto_type"])
    if event.key == "w":
        filename = time.strftime(FILE_NAME_FORMAT) + ".png"
        plt.savefig(filename)
        print("saved to:", filename)
    if event.key == "r":
        filename = time.strftime(FILE_NAME_FORMAT) + ".npy"
        np.save(filename, camera.frame_raw_u16.reshape(camera.height + 4, camera.width))
        print("saved to:", filename)
    if event.key == "v":
        if csv_filename is None:
            csv_filename = time.strftime(FILE_NAME_FORMAT) + ".csv"
            with open(csv_filename, "w", newline="") as f:
                header = ["time"]
                header += [
                    f"{a} {x}"
                    for a in temp_annotations["std"].keys()
                    for x in ["x", "y", "val"]
                ]  # t, tmin x, tmin y, tmin val, etc
                header += [
                    f"Point{i} {x}"
                    for i, key in enumerate(temp_annotations["user"].keys())
                    for x in ["x", "y", "val"]
                ]
                csv.writer(f).writerow(header)
            print("Annotation recording started in:", csv_filename)
        else:
            print("Annotation recording  saved  in:", csv_filename)
            csv_filename = None

    if event.key in [",", "."]:
        if event.key == ".":
            cmaps_idx = (cmaps_idx + 1) % len(cmaps)
        else:
            cmaps_idx = (cmaps_idx - 1) % len(cmaps)
        print("color map:", cmaps[cmaps_idx])
        im.set_cmap(cmaps[cmaps_idx])
        update_colormap = True
    if event.key in ["k", "l"]:
        if event.key == "k":
            camera.temperature_range_normal()
        else:
            camera.temperature_range_high()
        camera.wait_for_range_application()  # this takes care of calibration as well
        update_colormap = True
    if event.key in ["left", "right", "up", "down"]:
        exposure["auto"] = False
        T_cent = int((exposure["T_min"] + exposure["T_max"]) / 2)
        d = int(exposure["T_max"] - T_cent)
        if event.key == "up":
            T_cent += exposure["T_margin"] / 2
        if event.key == "down":
            T_cent -= exposure["T_margin"] / 2
        if event.key == "left":
            d -= exposure["T_margin"] / 2
        if event.key == "right":
            d += exposure["T_margin"] / 2
        d = max(d, exposure["T_margin"])
        exposure["T_min"] = T_cent - d
        exposure["T_max"] = T_cent + d
        print(
            "auto exposure off, T_min:",
            exposure["T_min"],
            "T_cent:",
            T_cent,
            "T_max:",
            exposure["T_max"],
        )
        update_colormap = True


mouse_action_pos = (0, 0)
mouse_action = None


def onclick(event):
    global mouse_action, mouse_action_pos
    if event.inaxes == ax:
        pos = (int(event.xdata), int(event.ydata))
        if event.button == MouseButton.RIGHT:
            print("add user temperature annotation at pos:", pos)
            temp_annotations["user"][pos] = "white"
        if event.button == MouseButton.LEFT:
            if utils.inRoi(annotations.roi, pos, frame.shape):
                mouse_action = "move_roi"
                mouse_action_pos = (
                    annotations.roi[0][0] - pos[0],
                    annotations.roi[0][1] - pos[1],
                )
            else:
                mouse_action = "create_roi"
                mouse_action_pos = pos
                annotations.set_roi((pos, (0, 0)))


def onmotion(event):
    global mouse_action, mouse_action_pos, roi
    if event.inaxes == ax and event.button == MouseButton.LEFT:
        pos = (int(event.xdata), int(event.ydata))
        if mouse_action == "create_roi":
            w, h = pos[0] - mouse_action_pos[0], pos[1] - mouse_action_pos[1]
            roi = (mouse_action_pos, (w, h))
            annotations.set_roi(roi)
        if mouse_action == "move_roi":
            roi = (
                (pos[0] + mouse_action_pos[0], pos[1] + mouse_action_pos[1]),
                annotations.roi[1],
            )
            annotations.set_roi(roi)


anim = animation.FuncAnimation(fig, animate_func, interval=1000 / fps, blit=True)
fig.canvas.mpl_connect("button_press_event", onclick)
fig.canvas.mpl_connect("motion_notify_event", onmotion)
fig.canvas.mpl_connect("key_press_event", press)

print_help()
plt.show()
stop_capture(lock_in_thread)
camera.release()
