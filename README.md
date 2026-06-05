# Periscope

Webcam-based head tracking built for **Forza Horizon 6**. Look into the apex without taking your hands off the wheel — no TrackIR, no extra hardware.

**[⬇ Download Periscope.exe](https://github.com/Nickxmodz/Periscope/releases/latest/download/Periscope.exe)** &nbsp;·&nbsp; **[→ Project site & live demo](https://Nickxmodz.github.io/Periscope/)**

[![Download](https://img.shields.io/github/v/release/Nickxmodz/Periscope?label=download&logo=windows&style=for-the-badge)](https://github.com/Nickxmodz/Periscope/releases/latest/download/Periscope.exe)

## What it does

Reads your head yaw from any webcam via MediaPipe, maps it to mouse movement, and drives Forza Horizon 6's cockpit free-look. Runs locally, modifies no game files, distributed as a single Windows executable.

## Quick start

### From a release (recommended)

1. [**Download `Periscope.exe`**](https://github.com/Nickxmodz/Periscope/releases/latest/download/Periscope.exe) (or browse [all releases](../../releases)).
2. Launch — Windows will ask for camera permission. Allow it.
3. In your game, switch to cockpit/interior view and bind free-look to Right Mouse Button.
4. Press **F9** to start tracking.

### From source

```
pip install customtkinter pillow opencv-python mediapipe numpy
python HeadTracker.py
```

The MediaPipe model auto-downloads on first run. Settings save to `%APPDATA%\Periscope\settings.json`.

## Hotkeys

| Key  | Action                                                  |
|------|---------------------------------------------------------|
| F8   | Emergency stop — kills tracking, releases the mouse     |
| F9   | Toggle tracking                                         |
| F10  | Recenter — sets current head pose as neutral            |

## Building the executable

```
pip install pyinstaller
build.bat
```

Output: `dist\Periscope.exe`. First launch takes ~10–15 seconds while the bundled MediaPipe payload unpacks.

## Tuning

Defaults are tuned for "glance" behavior — dormant inside a ±15° comfort range, subtle pan past that. Tuning sliders are in the right panel of the app. See the [project site](https://Nickxmodz.github.io/Periscope/#install) for tuning and troubleshooting guidance.

## Reporting bugs

Open an [issue](../../issues) and include:

- Windows version
- Webcam (built-in laptop vs external USB)
- The `fps` reading from the app's status bar
- What you did, what happened, what you expected

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

Not affiliated with, endorsed by, or associated with Playground Games, Turn 10, Xbox Game Studios, or Microsoft. Forza and Forza Horizon are trademarks of Microsoft.
