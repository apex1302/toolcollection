import tkinter as tk
from tkinter import filedialog, messagebox
import piexif
from PIL import Image
import random
import os

def random_gps():
    lat = random.uniform(-90, 90)
    lon = random.uniform(-180, 180)
    return lat, lon

def random_camera():
    cameras = [
        ("Canon", "EOS 5D Mark IV"),
        ("Nikon", "D850"),
        ("Sony", "Alpha A7 III"),
        ("Fujifilm", "X-T4"),
        ("Olympus", "OM-D E-M1 Mark III")
    ]
    return random.choice(cameras)

def random_exposure():
    iso = random.choice([100, 200, 400, 800, 1600, 3200])
    shutter_speed = random.choice([(1, 60), (1, 125), (1, 250), (1, 500), (1, 1000)])
    aperture = random.choice([(28, 10), (35, 10), (40, 10), (56, 10), (80, 10)])
    return iso, shutter_speed, aperture

def modify_exif(file_path):
    img = Image.open(file_path)
    exif_dict = piexif.load(img.info.get("exif", b""))
    
    lat, lon = random_gps()
    make, model = random_camera()
    iso, shutter_speed, aperture = random_exposure()
    
    exif_dict["0th"][piexif.ImageIFD.Make] = make.encode("utf-8")
    exif_dict["0th"][piexif.ImageIFD.Model] = model.encode("utf-8")
    exif_dict["Exif"][piexif.ExifIFD.ISOSpeedRatings] = iso
    exif_dict["Exif"][piexif.ExifIFD.ExposureTime] = shutter_speed
    exif_dict["Exif"][piexif.ExifIFD.FNumber] = aperture
    
    gps_ifd = {
        piexif.GPSIFD.GPSLatitudeRef: "N".encode() if lat >= 0 else "S".encode(),
        piexif.GPSIFD.GPSLatitude: [(abs(int(lat)), 1), (int((abs(lat) % 1) * 60), 1), (int(((abs(lat) * 60) % 1) * 60000), 1000)],
        piexif.GPSIFD.GPSLongitudeRef: "E".encode() if lon >= 0 else "W".encode(),
        piexif.GPSIFD.GPSLongitude: [(abs(int(lon)), 1), (int((abs(lon) % 1) * 60), 1), (int(((abs(lon) * 60) % 1) * 60000), 1000)],
    }
    
    exif_dict["GPS"] = gps_ifd
    new_exif_bytes = piexif.dump(exif_dict)
    
    new_file = os.path.splitext(file_path)[0] + "_modified.jpg"
    img.save(new_file, "jpeg", exif=new_exif_bytes)
    messagebox.showinfo("Success", f"Modified EXIF saved as: {new_file}")

def select_file():
    file_path = filedialog.askopenfilename(filetypes=[("JPEG Files", "*.jpg;*.jpeg")])
    if file_path:
        modify_exif(file_path)

# GUI Setup
root = tk.Tk()
root.title("EXIF Randomizer")
root.geometry("300x150")

label = tk.Label(root, text="Select a JPG file to modify EXIF")
label.pack(pady=10)

button = tk.Button(root, text="Choose File", command=select_file)
button.pack(pady=20)

root.mainloop()

