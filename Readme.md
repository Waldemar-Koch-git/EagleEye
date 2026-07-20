# 🦅EagleEye👁

**Lightweight, cross-platform multi-camera stream viewer, built with Python and PyQt6.**

EagleEye lets you monitor several IP cameras at once in a single window - RTSP, MJPEG, HTTP video or plain snapshot polling - with recording, digital zoom, audio playback and a layout that adapts to however you size the window.

![Preview](./images_/preview.jpg)

---

## Supported Stream Types

| Type | Description |
|------|-------------|
| `snapshot_jpeg` | Single JPEG image, polled at an interval |
| `mjpeg` | Server-push `multipart/x-mixed-replace` |
| `http_video` | Progressive H.264/H.265 over HTTP (via FFmpeg/PyAV) |
| `rtsp_tcp` | RTSP over TCP (H.264/H.265) |
| `rtsp_udp` | RTSP over UDP unicast (H.264/H.265) |
| `rtsp_multicast` | RTSP over UDP multicast (H.264/H.265) |
| `auto` | Automatic detection via HTTP headers / URL heuristics |

---

## Installation

```bash
git clone https://github.com/Waldemar-Koch-git/EagleEye
cd EagleEye
pip install -r requirements.txt
```

PyAV ships FFmpeg as prebuilt wheels (Linux/Windows/macOS), so a separate
FFmpeg install usually **isn't** required.

**Audio playback** additionally needs the optional `sounddevice` package
(uses PortAudio, bundled with most wheels):

```bash
pip install sounddevice
```

If `sounddevice` isn't installed, EagleEye keeps working normally - audio
is simply skipped (video, recording, etc. are unaffected).

### Requirements
- Python 3.10+
- PyQt6 (UI)
- av / PyAV (decoding, includes FFmpeg)
- OpenCV headless (JPEG decode/encode for snapshot & MJPEG)
- numpy, requests
- sounddevice *(optional, for audio playback)*

---

## Getting Started

```bash
python main.py
```

Use the toolbar button **"+ Add Stream"** to set up a camera (URL, type,
credentials, decode scale, target FPS, rotation, reconnect behavior,
optional sub-stream). Right-clicking a tile opens a context menu
(Settings, Enable/Disable, switch Main/Sub stream, Audio, Recording, Remove).

**Fullscreen:** Double-click a tile to show that one stream full-size in
the grid, hiding all other tiles. Double-click again (on the now-fullscreen
tile) to return to the normal grid view.

**Camera overview:** The "Camera overview" toolbar button opens a separate,
non-modal window listing all configured cameras (name, type, main URL,
sub-stream, active state). Cameras can be added, edited, enabled/disabled
and removed from there - changes apply immediately to the live view.

**Main / sub stream:** In the settings dialog you can set a sub-stream URL
in addition to the main one (e.g. a lower-resolution RTSP variant).
Right-click a tile → "Switch to sub/main stream" to switch live between the
two (the decode worker is restarted cleanly).

**Enable / disable a stream:** Via the tile's right-click menu or the
camera overview (the worker thread is stopped or restarted - no background
load for disabled cameras). There's deliberately no button directly on the
tile, to avoid accidental clicks.

**Always on top:** Toolbar toggle to keep the window above all others
(e.g. for a permanently visible camera monitor).

**Layout:**
- *Dynamic*: columns/rows are chosen from the actual window size (not a
  rigid `ceil(sqrt(n))`) - it picks the combination that gives the tiles
  (assumed 16:9) the most area. A narrow/tall window therefore automatically
  gets more rows and fewer columns instead of squeezing everything sideways.
  Adjusts live while you drag the window edge.
- *Fixed*: you set columns/rows yourself; extra streams are hidden instead
  of breaking the layout.
- Tiles can be reordered via **drag & drop** directly in the grid.

**Hide disabled:** Toolbar toggle "Hide disabled" removes paused cameras
from the grid entirely, instead of leaving them black and taking up space -
the layout automatically adapts to the remaining active streams.

**Compact mode:** Toolbar toggle or **F11** hides the toolbars and status
bar so only the camera tiles remain visible (e.g. for a clean monitoring
screen). F11 always switches back, even while the toolbars are hidden
(a reminder is also shown permanently in the title bar).

**Hardware decoding:** Enable per stream (`hw_accel` in the settings
dialog). Automatically uses VideoToolbox (macOS), VAAPI/NVDEC (Linux) or
NVDEC/D3D11VA (Windows) via PyAV/FFmpeg, and falls back cleanly to software
decoding if unavailable. Only applies to RTSP/HTTP video, not
Snapshot/MJPEG.

**Recording:** Right-click a tile → "Start/stop recording" records the
stream as MP4 to `~/.EagleEye/recordings/` (filename from camera name +
timestamp). Works uniformly across all stream types since it hooks into the
already-decoded frames (re-encoding rather than pure remuxing). An active
recording shows a small "REC" indicator on the tile and stops cleanly
automatically when the stream is disabled.

**FPS display:** Hidden by default to keep tiles clean. Can be toggled per
camera via right-click on the tile → "Show FPS display", or preset when
adding/editing a camera via the `show_fps` checkbox in the settings dialog.

**Zoom:** Hold the mouse over a tile and scroll with **Ctrl+mouse wheel**
to enlarge/shrink the visible crop, centered on the cursor position
(digital zoom up to 6x). Display-side only - only the already-decoded frame
is cropped before drawing, with no effect on decoding, network load or
recording. While zoomed, the crop can additionally be moved by holding
**Ctrl+left-click and dragging** (deliberately tied to Ctrl so it doesn't
clash with the normal left-click drag used to reorder tiles). Right-click →
"Reset zoom" (only visible when the tile is currently zoomed) returns to 1x.

**Audio:** Enable per stream (`audio_enabled` in the settings dialog, only
relevant for RTSP/HTTP video with an audio track). Deliberately only one
stream plays audio at a time to avoid mixing audio tracks - enabling audio
for one stream automatically mutes all others. Requires the optional
`sounddevice` package (see Installation).

**Remember window state:** Toolbar toggle "Remember window state" - when
active, "Save" additionally stores window position, size and whether
compact mode was active; both are restored automatically on next launch.
When the toggle is off, EagleEye always starts at the default size in
normal mode.

Configuration is written via "Save" to `~/.EagleEye/config.json` and
loaded automatically on the next start.

---

## Architecture / Performance Principles

- **One thread per stream** (no GIL bottleneck, since decoding runs in
  native code - FFmpeg via PyAV, or OpenCV/libjpeg - which releases the GIL)
- **"Latest-frame-only" buffer** instead of a queue: no backlog of old
  frames ever builds up → constant low latency and constant memory usage,
  regardless of how fast the source delivers frames (`frame_buffer.py`)
- The GUI **polls** the newest frame via `QTimer` instead of firing a Qt
  signal on every decoded frame → far less event-loop load with many
  simultaneous streams; individually cappable per stream to a target FPS
  (`max_fps` in the stream settings)
- Cross-thread status messages go through a Qt signal (thread-safe
  marshalling by Qt); worker threads never touch the GUI directly
- Per-stream **decode scaling** (`scale`) to save CPU/bandwidth with many
  tiles (a small tile doesn't need full resolution)
- Automatic **reconnect** with a configurable delay on connection loss

## Project Structure

```
EagleEye_project/
├── main.py                     # Application entry point
├── requirements.txt
└── EagleEye/
    ├── stream_types.py         # Stream type enum
    ├── stream_config.py        # Per-stream configuration (dataclass, incl. sub-stream)
    ├── frame_buffer.py         # Thread-safe latest-frame buffer
    ├── detector.py             # Automatic stream type detection
    ├── stream_worker.py        # Decoding thread (all stream types, HW decoding, audio)
    ├── recorder.py             # Records a stream to an MP4 file
    ├── video_widget.py         # Per-camera display widget (double-click=fullscreen, REC, drag&drop)
    ├── settings_dialog.py      # Per-stream settings dialog
    ├── camera_manager_dialog.py# Camera overview (table of all streams)
    ├── main_window.py          # Main window, grid layout, persistence
    └── i18n.py                 # Internationalization (English default, switchable to German)
```

---

## Language

The UI defaults to **English** and can be switched at runtime from the
"Window" toolbar - currently **German** is also available. The active
language is remembered between sessions (`~/.EagleEye/language.json`).
Adding another language means adding one more dictionary to
`EagleEye/i18n.py`.

---

## Roadmap

**Already implemented:**
- ✅ Disabled cameras can be hidden (see "Hide disabled")
- ✅ Hardware decoding (NVDEC/VideoToolbox/VAAPI)
- ✅ Recording per stream
- ✅ Audio playback for streams with an audio track
- ✅ Drag & drop tile reordering
- ✅ Compact mode (F11) - only camera tiles visible, reversible
- ✅ Dynamic grid based on actual window size (instead of a rigid
  `ceil(sqrt(n))`), tiles stack sensibly in narrow windows
- ✅ Window position, size and compact mode optionally saved/restored
- ✅ Persistence of window geometry / toolbar state between sessions
- ✅ Per-tile fullscreen via double-click (double-click again switches back)
- ✅ Zoom via Ctrl+mouse wheel; pan the zoomed image with Ctrl+drag

**Open ideas:**
- Multi-channel audio / simultaneous playback of several streams
  (currently deliberately limited to one at a time)
- PTZ control (ONVIF) for supported cameras
- Motion detection / event-triggered recording
- Multiple camera groups/profiles (e.g. "Indoor"/"Outdoor") with their own
  layout

---

## License

This project is licensed under a custom non-commercial license - see
[LICENSE](LICENSE). Non-commercial use, copying and modification are
permitted; commercial use requires prior written permission from the
author.

**Disclaimer:** This software is provided "as is". No liability is
accepted for data loss or security incidents. Use at your own risk.
