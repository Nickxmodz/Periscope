"""
HeadTracker.py — Periscope: webcam head tracking for Forza Horizon 6.

Two parts, kept separate on purpose:
  * TrackerEngine - all the messy stuff (camera, MediaPipe, mouse output,
    hotkeys, threading). You rarely need to touch this.
  * App - the CustomTkinter window. To add or change a control, edit the
    SLIDERS / CHECKS lists near the top of the App section. That's it.

Install once:
    pip install customtkinter pillow opencv-python mediapipe numpy

Run:
    python HeadTracker.py

Global hotkeys still work while the GAME is focused:
    F9  start/stop tracking      F10 recenter      F8  stop tracking
"""

import os
import sys
import json
import time
import math
import threading
import ctypes
from ctypes import wintypes

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

import tkinter as tk
import customtkinter as ctk
from PIL import Image, ImageTk

# ============================ shared bits ============================
# When frozen by PyInstaller, bundled data files live in sys._MEIPASS (one-file
# mode) rather than next to __file__.
_APP_DIR = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(_APP_DIR, "face_landmarker.task")
MODEL_URL  = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
              "face_landmarker/float16/1/face_landmarker.task")
SETTINGS_PATH = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                             "Periscope", "settings.json")

# ---- Win32 mouse + key input via SendInput / GetAsyncKeyState ----
user32 = ctypes.windll.user32
MOUSEEVENTF_MOVE, MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0001, 0x0008, 0x0010
ULONG_PTR = wintypes.WPARAM

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]

def _send(flags, dx=0, dy=0):
    inp = INPUT(type=0, u=_U(mi=MOUSEINPUT(dx, dy, 0, flags, 0, 0)))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

def mouse_move(dx, dy=0): _send(MOUSEEVENTF_MOVE, int(dx), int(dy))
def rmb_down():           _send(MOUSEEVENTF_RIGHTDOWN)
def rmb_up():             _send(MOUSEEVENTF_RIGHTUP)
def key_down(vk):         return user32.GetAsyncKeyState(vk) & 0x8000 != 0

VK_F8, VK_F9, VK_F10 = 0x77, 0x78, 0x79

class OneEuro:
    def __init__(self, mincutoff, beta, dcutoff=2.5):
        self.mincutoff, self.beta, self.dcutoff = mincutoff, beta, dcutoff
        self.x_prev = self.dx_prev = None
        self.t_prev = None
    def _alpha(self, cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)
    def __call__(self, x, t):
        if self.t_prev is None:
            self.t_prev, self.x_prev, self.dx_prev = t, x, 0.0
            return x
        dt = max(t - self.t_prev, 1e-3)
        dx = (x - self.x_prev) / dt
        a_d = self._alpha(self.dcutoff, dt)
        dx_hat = a_d * dx + (1 - a_d) * self.dx_prev
        cutoff = self.mincutoff + self.beta * abs(dx_hat)
        a = self._alpha(cutoff, dt)
        x_hat = a * x + (1 - a) * self.x_prev
        self.t_prev, self.x_prev, self.dx_prev = t, x_hat, dx_hat
        return x_hat

def ensure_model():
    if os.path.exists(MODEL_PATH):
        return
    import urllib.request
    try:
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    except Exception as e:
        raise RuntimeError(f"Model download failed: {e}")

def yaw_from_matrix(M):
    R = np.asarray(M)[:3, :3]
    sy = math.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)
    return math.degrees(math.atan2(-R[2, 0], sy))

# ============================ tracking engine ============================
class TrackerEngine:
    """Runs the camera + tracking on its own thread. The GUI only reads
    state (latest_frame, yaw, state) and writes tunable params. Nothing here
    touches the UI, so the two never block each other."""

    def __init__(self):
        # tunable params (safe to write from the GUI thread at any time)
        self.cam_index = 0
        self.invert_yaw = False
        self.deadzone = 15.0
        self.yaw_max = 22.0
        self.look_span = 15.0
        self.curve = 1.0
        self.max_step = 15
        self.recenter_on_release = True
        self.release_frac = 0.6
        self.mincutoff = 0.8
        self.beta = 0.02
        self.lookahead_ms = 50.0       # predict yaw forward by this much to hide pipeline lag
        self.use_gpu = True            # try GPU delegate for MediaPipe (falls back to CPU)
        self.show_overlay = True
        self.tracking = False          # the F9 toggle: are we driving the mouse?

        # runtime state (GUI reads these)
        self.latest_frame = None
        self.yaw = None
        self.fps = 0.0
        self.cam_fps_reported = 0.0
        self.cam_resolution = ""
        self.delegate_mode = ""
        self.state = "OFF"             # OFF / ON / HELD
        self.error = None

        self._run = False
        self._thread = None
        self._recenter_request = False   # GUI sets this; engine consumes it next frame

    def start(self):
        if self._run:
            return
        self._run = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._run = False
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None

    def toggle_tracking(self):
        self.tracking = not self.tracking

    def _loop(self):
        try:
            ensure_model()
            cap = cv2.VideoCapture(self.cam_index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                self.error = f"Could not open webcam index {self.cam_index}"
                self._run = False
                return
            # 320x240@60 + MJPG + buffer=1. Quarter the pixels of 480p means MediaPipe
            # inference is ~4x faster on CPU — the difference between 15 fps and 60 fps
            # on most laptops. Face landmarking still works fine at this resolution.
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
            cap.set(cv2.CAP_PROP_FPS, 60)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 320
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 240
            self.cam_resolution = f"{w}x{h}"
            self.cam_fps_reported = cap.get(cv2.CAP_PROP_FPS)

            def _make_landmarker(use_gpu):
                delegate = (mp_python.BaseOptions.Delegate.GPU if use_gpu
                            else mp_python.BaseOptions.Delegate.CPU)
                opts = vision.FaceLandmarkerOptions(
                    base_options=mp_python.BaseOptions(
                        model_asset_path=MODEL_PATH, delegate=delegate),
                    running_mode=vision.RunningMode.VIDEO,
                    num_faces=1,
                    output_facial_transformation_matrixes=True)
                return vision.FaceLandmarker.create_from_options(opts)

            try:
                landmarker = _make_landmarker(self.use_gpu)
                self.delegate_mode = "GPU" if self.use_gpu else "CPU"
            except Exception:
                landmarker = _make_landmarker(False)   # GPU delegate not available — fall back
                self.delegate_mode = "CPU(fallback)"
        except Exception as e:
            self.error = str(e)
            self._run = False
            return

        oe = OneEuro(self.mincutoff, self.beta)
        rmb_held = False
        neutral = 0.0
        cur_px = 0.0
        send_accum = 0.0
        v_peak = 0.0          # decaying peak velocity, used to damp prediction on decel
        yaw_pred_lpf = None   # adaptive low-pass on prediction output (settle smoothing)
        hold_frames = 0       # consecutive frames below hold threshold
        hold_target = 0.0     # frozen target while hold-locked
        prev_f9 = prev_f10 = prev_f8 = False
        prev_tracking = False
        last_ts = -1
        fps_ema = 0.0
        last_frame_t = time.perf_counter()

        try:
            while self._run:
                ok, frame = cap.read()
                if not ok:
                    continue
                now = time.perf_counter()
                dt_frame = now - last_frame_t
                last_frame_t = now
                if dt_frame > 0:
                    inst_fps = 1.0 / dt_frame
                    fps_ema = inst_fps if fps_ema == 0 else fps_ema * 0.9 + inst_fps * 0.1
                    self.fps = fps_ema

                frame = cv2.flip(frame, 1)
                rgb = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                ts = int(time.perf_counter() * 1000)
                if ts <= last_ts:
                    ts = last_ts + 1
                last_ts = ts
                result = landmarker.detect_for_video(mp_img, ts)

                oe.mincutoff, oe.beta = self.mincutoff, self.beta
                yaw = None
                yaw_pred = None       # yaw + velocity * lookahead, used to drive the mouse
                lms = None
                if result.facial_transformation_matrixes:
                    # perf_counter is monotonic and sub-microsecond on Windows;
                    # time.time() can be coarse, making OneEuro's dt jittery and
                    # producing shake unrelated to actual head motion.
                    yaw = oe(yaw_from_matrix(result.facial_transformation_matrixes[0]), time.perf_counter())
                    # Velocity-gated prediction with decel damping:
                    #   gain_v   = quadratic ramp on |v| (kills micro-jitter amplification)
                    #   gain_dec = current_v / recent_peak_v (kills overshoot on stop —
                    #              the "shake no" loop where prediction keeps pushing
                    #              forward after your head has already stopped)
                    v = oe.dx_prev
                    v_abs = abs(v)
                    v_peak = max(v_abs, v_peak * 0.85)   # ~100ms decay of peak memory
                    v_full = 15.0
                    gain_v = min(1.0, (v_abs / v_full) ** 2)
                    gain_dec = v_abs / max(v_peak, 1e-3)
                    gain = gain_v * gain_dec
                    yaw_pred = yaw + v * (self.lookahead_ms / 1000.0) * gain

                    # Adaptive output smoothing keyed to raw |v|: alpha=0.9 during fast
                    # motion (~7ms TC, transparent), alpha=0.5 at rest (~24ms TC, gently
                    # damps held-look wobble without adding perceptible lag to slow turns).
                    if yaw_pred_lpf is None:
                        yaw_pred_lpf = yaw_pred
                    else:
                        alpha = 0.5 + 0.4 * min(1.0, v_abs / 30.0)
                        yaw_pred_lpf = alpha * yaw_pred + (1.0 - alpha) * yaw_pred_lpf
                    yaw_pred = yaw_pred_lpf
                    if result.face_landmarks:
                        lms = result.face_landmarks[0]
                self.yaw = yaw

                # global hotkeys (work even while the game is focused)
                f9, f10, f8 = key_down(VK_F9), key_down(VK_F10), key_down(VK_F8)
                if f9 and not prev_f9:
                    self.tracking = not self.tracking
                if f8 and not prev_f8:
                    self.tracking = False
                do_recenter = (f10 and not prev_f10) or self._recenter_request
                self._recenter_request = False
                prev_f9, prev_f10, prev_f8 = f9, f10, f8

                just_on = self.tracking and not prev_tracking

                if not self.tracking:
                    if rmb_held:
                        rmb_up(); rmb_held = False
                    cur_px = 0.0; send_accum = 0.0; hold_frames = 0
                    self.state = "OFF"
                else:
                    if just_on:
                        neutral = yaw if yaw is not None else neutral
                        cur_px = 0.0; send_accum = 0.0; hold_frames = 0
                        if not self.recenter_on_release:
                            rmb_down(); rmb_held = True
                    if do_recenter and yaw is not None:
                        neutral = yaw

                    if yaw_pred is not None:
                        d = yaw_pred - neutral
                        if self.invert_yaw:
                            d = -d
                        d = max(-self.yaw_max, min(self.yaw_max, d))

                        if self.recenter_on_release:
                            if not rmb_held and abs(d) > self.deadzone:
                                rmb_down(); rmb_held = True
                                cur_px = 0.0; send_accum = 0.0; hold_frames = 0
                            elif (rmb_held and abs(d) < self.deadzone * self.release_frac
                                  and abs(cur_px) <= self.max_step):
                                rmb_up(); rmb_held = False
                                cur_px = 0.0; send_accum = 0.0; hold_frames = 0

                        if rmb_held:
                            denom = max(self.yaw_max - self.deadzone, 1e-3)
                            mag = max(abs(d) - self.deadzone, 0.0)
                            norm = min(mag / denom, 1.0) ** self.curve
                            target_raw = math.copysign(norm, d) * self.look_span

                            # Hold lock: once velocity has been below threshold for
                            # ~130ms, freeze the target so MediaPipe's per-frame pose
                            # noise (worse at angles) can't drive the mouse at all.
                            # The instant real motion resumes, the lock releases.
                            HOLD_V_THRESH = 4.0   # deg/s
                            HOLD_FRAMES_REQ = 8   # ~130ms at 60fps
                            if v_abs < HOLD_V_THRESH:
                                hold_frames += 1
                                if hold_frames == HOLD_FRAMES_REQ:
                                    hold_target = target_raw    # latch on entry
                            else:
                                hold_frames = 0
                            target = hold_target if hold_frames >= HOLD_FRAMES_REQ else target_raw

                            step = max(-self.max_step, min(self.max_step, target - cur_px))
                            send_accum += step
                            isend = int(send_accum)
                            if isend != 0:
                                mouse_move(isend)
                                cur_px += isend
                                send_accum -= isend
                    self.state = "HELD" if rmb_held else "ON"

                prev_tracking = self.tracking

                if self.show_overlay and lms is not None and yaw is not None:
                    for lm in lms:
                        cv2.circle(frame, (int(lm.x * w), int(lm.y * h)), 1, (0, 255, 0), -1)
                    nose = lms[1]
                    cx, cy = int(nose.x * w), int(nose.y * h)
                    a = math.radians(yaw)
                    cv2.line(frame, (cx, cy),
                             (cx + int(80 * math.sin(a)), cy - int(80 * math.cos(a))),
                             (255, 0, 0), 3)
                self.latest_frame = frame
        finally:
            if rmb_held:
                rmb_up()
            cap.release()
            landmarker.close()

# ============================ GUI ============================
# To add/remove a control, edit these two lists. Nothing else needs changing.
# (attr_name, label, min, max, is_integer)
SLIDERS = [
    ("deadzone",     "Deadzone (deg)",        0.0, 20.0, False),
    ("yaw_max",      "Full-look angle (deg)", 5.0, 70.0, False),
    ("look_span",    "Max view travel (px)",  5.0, 200.0, False),
    ("curve",        "Response curve",        1.0, 4.0,  False),
    ("max_step",     "Smoothness / step (px)", 1.0, 50.0, True),
    ("release_frac", "Release threshold",     0.3, 0.9,  False),
    ("mincutoff",    "Smoothing: min cutoff", 0.1, 5.0,  False),
    ("beta",         "Smoothing: beta",       0.0, 0.5,  False),
    ("lookahead_ms", "Lookahead (ms)",        0.0, 250.0, False),
]
# (attr_name, label)
CHECKS = [
    ("invert_yaw",          "Invert left / right"),
    ("recenter_on_release", "Recenter on release"),
    ("show_overlay",        "Show face overlay"),
    ("use_gpu",             "Use GPU (restart camera)"),
]
SAVE_KEYS = [s[0] for s in SLIDERS] + [c[0] for c in CHECKS] + ["cam_index"]

PREVIEW_W, PREVIEW_H = 480, 360

class App(ctk.CTk):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.title("Periscope")
        self.geometry("900x600")
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.slider_widgets = {}   # attr -> (slider, value_label)
        self.check_vars = {}       # attr -> BooleanVar

        # ---- left: live preview + big controls ----
        left = ctk.CTkFrame(self)
        left.pack(side="left", fill="both", expand=True, padx=12, pady=12)

        self.preview = tk.Label(left, width=PREVIEW_W, height=PREVIEW_H, bg="#1a1a1a")
        self.preview.pack(pady=(4, 8))

        self.status = ctk.CTkLabel(left, text="Starting camera...",
                                   font=ctk.CTkFont(size=16, weight="bold"))
        self.status.pack(pady=4)

        self.track_btn = ctk.CTkButton(left, text="Start Tracking", height=44,
                                       font=ctk.CTkFont(size=16, weight="bold"),
                                       command=self.engine.toggle_tracking)
        self.track_btn.pack(pady=6, padx=20, fill="x")

        row = ctk.CTkFrame(left, fg_color="transparent")
        row.pack(pady=2, padx=20, fill="x")
        ctk.CTkButton(row, text="Recenter (F10)",
                      command=self._recenter).pack(side="left", expand=True, fill="x", padx=(0, 4))
        ctk.CTkButton(row, text="Restart Camera",
                      command=self._restart_cam).pack(side="left", expand=True, fill="x", padx=(4, 0))

        ctk.CTkLabel(left, text="Hotkeys: F9 start/stop   F10 recenter   F8 stop",
                     text_color="#888").pack(pady=(6, 2))

        # ---- right: tuning panel (scrollable so it never gets cut off) ----
        right = ctk.CTkScrollableFrame(self, width=320, label_text="Tuning")
        right.pack(side="right", fill="y", padx=(0, 12), pady=12)

        for attr, label, lo, hi, is_int in SLIDERS:
            self._make_slider(right, attr, label, lo, hi, is_int)

        for attr, label in CHECKS:
            self._make_check(right, attr, label)

        cam_row = ctk.CTkFrame(right, fg_color="transparent")
        cam_row.pack(fill="x", pady=(12, 4))
        ctk.CTkLabel(cam_row, text="Camera index").pack(side="left")
        self.cam_entry = ctk.CTkEntry(cam_row, width=60)
        self.cam_entry.insert(0, str(self.engine.cam_index))
        self.cam_entry.pack(side="right")

        ctk.CTkButton(right, text="Reset defaults",
                      command=self._reset_defaults).pack(fill="x", pady=(12, 4))

        # init
        self.load_settings()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.engine.start()
        self.after(33, self._tick)

    # ---- widget builders ----
    def _make_slider(self, parent, attr, label, lo, hi, is_int):
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="x", pady=6)
        head = ctk.CTkFrame(wrap, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(head, text=label).pack(side="left")
        val = ctk.CTkLabel(head, text="", width=50)
        val.pack(side="right")
        steps = int(hi - lo) if is_int else 0
        slider = ctk.CTkSlider(wrap, from_=lo, to=hi,
                               number_of_steps=(steps if steps else None),
                               command=lambda v, a=attr, i=is_int, lb=val: self._on_slider(a, v, i, lb))
        slider.pack(fill="x", pady=(2, 0))
        self.slider_widgets[attr] = (slider, val, is_int)

    def _make_check(self, parent, attr, label):
        var = ctk.BooleanVar(value=bool(getattr(self.engine, attr)))
        chk = ctk.CTkCheckBox(parent, text=label, variable=var,
                              command=lambda a=attr, v=var: setattr(self.engine, a, v.get()))
        chk.pack(anchor="w", pady=6)
        self.check_vars[attr] = var

    # ---- callbacks ----
    def _on_slider(self, attr, value, is_int, label):
        v = int(round(float(value))) if is_int else round(float(value), 3)
        setattr(self.engine, attr, v)
        label.configure(text=str(v) if is_int else f"{v:.2f}")

    def _recenter(self):
        self.engine._recenter_request = True

    def _reset_defaults(self):
        fresh = TrackerEngine()
        for k in SAVE_KEYS:
            if k == "cam_index":
                continue
            setattr(self.engine, k, getattr(fresh, k))
        for attr, *_ in [(s[0],) for s in SLIDERS]:
            self._set_slider(attr, getattr(self.engine, attr))
        for attr in self.check_vars:
            self.check_vars[attr].set(bool(getattr(self.engine, attr)))

    def _restart_cam(self):
        try:
            idx = int(self.cam_entry.get())
        except ValueError:
            return
        self.engine.stop()
        self.engine.error = None
        self.engine.cam_index = idx
        self.engine.start()

    def _set_slider(self, attr, value):
        slider, label, is_int = self.slider_widgets[attr]
        slider.set(value)
        label.configure(text=str(int(value)) if is_int else f"{float(value):.2f}")

    # ---- periodic UI refresh (main thread only) ----
    def _tick(self):
        eng = self.engine
        if eng.error:
            self.status.configure(text=f"Error: {eng.error}")
        else:
            frame = eng.latest_frame
            if frame is not None:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(rgb).resize((PREVIEW_W, PREVIEW_H))
                self._photo = ImageTk.PhotoImage(img)
                self.preview.configure(image=self._photo)
            ytxt = f"{eng.yaw:+.1f}" if eng.yaw is not None else "--"
            self.status.configure(
                text=f"{eng.state}   yaw {ytxt}   {eng.fps:.0f}/{eng.cam_fps_reported:.0f} fps   "
                     f"{eng.cam_resolution}   {eng.delegate_mode}")
            self.track_btn.configure(text="Stop Tracking" if eng.tracking else "Start Tracking")
        self.after(33, self._tick)

    # ---- settings persistence ----
    def save_settings(self):
        data = {k: getattr(self.engine, k) for k in SAVE_KEYS}
        try:
            os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
            with open(SETTINGS_PATH, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def load_settings(self):
        data = {}
        if os.path.exists(SETTINGS_PATH):
            try:
                with open(SETTINGS_PATH) as f:
                    data = json.load(f)
            except Exception:
                data = {}
        # apply to engine, then sync widgets to whatever the engine now holds
        for k in SAVE_KEYS:
            if k in data:
                setattr(self.engine, k, data[k])
        for attr, *_ in [(s[0],) for s in SLIDERS]:
            self._set_slider(attr, getattr(self.engine, attr))
        for attr in self.check_vars:
            self.check_vars[attr].set(bool(getattr(self.engine, attr)))
        self.cam_entry.delete(0, "end")
        self.cam_entry.insert(0, str(self.engine.cam_index))

    def on_close(self):
        self.save_settings()
        self.engine.stop()
        self.destroy()

if __name__ == "__main__":
    App(TrackerEngine()).mainloop()