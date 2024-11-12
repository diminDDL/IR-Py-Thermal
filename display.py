#!/usr/bin/env python3

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.widgets import Button

# Load the numpy file
data = np.load("./2024-06-21_23-02-55.npy")


# Function to get the pixel number
def get_pixel_number(x, y, width, height, origin=(0, 0)):
    zero_x, zero_y = origin
    pixel_number = (y - zero_y) * width + (x - zero_x)
    return pixel_number


# Click event handler
def onclick(event):
    global arrow, rect
    x, y = int(event.xdata), int(event.ydata)
    height, width = data.shape

    if x >= 0 and y >= 0 and x < width and y < height:
        pixel_number = get_pixel_number(x, y, width, height, origin=(zero_x, zero_y))
        pixel_value = data[y, x]
        print(f"Pixel ({x}, {y}) has number: {pixel_number} and value: {pixel_value}")

        # Clear the previous highlight
        if arrow:
            arrow.remove()
        if rect:
            rect.remove()

        # Draw an arrow pointing to the clicked pixel with detailed information
        arrow = ax.annotate(
            f"({x}, {y})\nNumber: {pixel_number}\nValue: {pixel_value}",
            xy=(x, y),
            xytext=(x + 10, y + 10),
            arrowprops=dict(facecolor="red", shrink=0.05),
            bbox=dict(boxstyle="round,pad=0.3", edgecolor="red", facecolor="white"),
        )

        # Draw a rectangle around the clicked pixel
        rect = plt.Rectangle(
            (x - 0.5, y - 0.5), 1, 1, edgecolor="red", facecolor="none"
        )
        ax.add_patch(rect)

        fig.canvas.draw()


# Function to highlight all non-zero pixels
def highlight_non_zero(event):
    global scatter
    if scatter:
        scatter.remove()
        scatter = None
    else:
        non_zero_pixels = np.argwhere(data != 0)
        y, x = non_zero_pixels[:, 0], non_zero_pixels[:, 1]
        scatter = ax.scatter(x, y, color="blue", s=10)

    fig.canvas.draw()


# Load and display the image
fig, ax = plt.subplots()
plt.subplots_adjust(bottom=0.2)  # Adjust to make space for the button
ax.imshow(data, cmap="gray")

# Define origin
zero_x, zero_y = 0, 192  # Adjust these values to set a different origin

# Initialize annotation and rectangle variables
arrow = None
rect = None
scatter = None

# Connect the click event
cid = fig.canvas.mpl_connect("button_press_event", onclick)

# Add a button to highlight non-zero pixels
ax_button = plt.axes([0.8, 0.05, 0.1, 0.075])
btn = Button(ax_button, "Toggle Non-Zero")
btn.on_clicked(highlight_non_zero)

# plt.tight_layout()

plt.show()
