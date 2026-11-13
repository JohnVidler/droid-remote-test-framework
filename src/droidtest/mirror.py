"""Live screen mirror + input recorder (Tk).

    uv run droidtest mirror [--scale 3] [--interval 0] [--load rec.json]

In the window:
  * left-click            tap
  * click-hold (>0.5 s)   long press
  * click-drag            swipe
  * right-click           Back
  * scroll wheel          scroll (swipe)
  * side panel            Back/Home/Recents, volume, power, text entry,
                          record / replay / save / load, step list editing

With "Record" on, every action is appended to the step list. Tick
"Record phone touches" to also capture gestures made with your finger on the
phone itself (via getevent). Saving writes both <name>.json (replayable with
`droidtest replay`) and <name>.py (a standalone droidtest script).
"""

from __future__ import annotations

import base64
import concurrent.futures
import math
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .adb import Device
from .recording import Recording, Step, classify

REC_DIR = Path("artifacts/recordings")


class MirrorApp:
    def __init__(self, device: Device, scale: int | None = None, interval: float = 0.0,
                 load: str | None = None):
        self.d = device
        self.interval = interval
        self.scale = scale              # integer subsample factor; None = fit to screen
        self.root = tk.Tk()
        self.root.title(f"droidtest mirror: {device.model} ({device.serial})")

        # shared state between the capture thread and the Tk thread
        self._frame: bytes | None = None
        self._frame_id = 0
        self._shown_id = 0
        self._stop = threading.Event()
        self._calls: queue.SimpleQueue = queue.SimpleQueue()  # thread -> Tk thread
        self._kick = threading.Event()      # request an immediate re-capture
        self._io = concurrent.futures.ThreadPoolExecutor(max_workers=1)  # ordered adb input
        self._orientation = device.orientation()
        self._raw_ok = True

        self.dev_w, self.dev_h = device.screen_size()
        self.factor = 1                     # device px per canvas px
        self.photo: tk.PhotoImage | None = None
        self.fps = 0.0

        self.recording = Recording(self.dev_w, self.dev_h, model=device.model)
        self.rec_start = time.monotonic()
        self.listener = None
        self.replaying = False

        self._press: tuple[int, int, float] | None = None
        self._drag_line = None
        self._wheel_accum = 0
        self._wheel_job = None
        self._touch_marker = None

        self._build_ui()
        if load:
            self._load(Path(load))

        threading.Thread(target=self._capture_loop, daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(50, self._tick)

    # =================================================================== UI
    def _build_ui(self) -> None:
        self.canvas = tk.Canvas(self.root, bg="#111", highlightthickness=0, cursor="crosshair")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.create_text(200, 300, text="Waiting for first frame...", fill="#aaa", tags="placeholder")

        side = ttk.Frame(self.root, padding=8)
        side.grid(row=0, column=1, sticky="ns")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # --- device controls
        nav = ttk.LabelFrame(side, text="Device", padding=6)
        nav.pack(fill="x")
        for i, (label, key) in enumerate([("◀ Back", "BACK"), ("● Home", "HOME"), ("▤ Recents", "APP_SWITCH"),
                                          ("Vol +", "VOLUME_UP"), ("Vol −", "VOLUME_DOWN"), ("⏻ Power", "POWER")]):
            ttk.Button(nav, text=label, width=9, command=lambda k=key: self.send_key(k)).grid(
                row=i // 3, column=i % 3, padx=2, pady=2)
        ttk.Button(nav, text="Notifications", command=lambda: self._do(self.d.open_notifications)).grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=2, pady=2)
        ttk.Button(nav, text="Collapse", command=lambda: self._do(self.d.collapse_status_bar)).grid(
            row=2, column=2, sticky="ew", padx=2, pady=2)

        txt = ttk.LabelFrame(side, text="Type text", padding=6)
        txt.pack(fill="x", pady=(8, 0))
        self.text_var = tk.StringVar()
        entry = ttk.Entry(txt, textvariable=self.text_var, width=24)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda e: self.send_text())
        ttk.Button(txt, text="Send", command=self.send_text).pack(side="left", padx=(4, 0))

        # --- recording
        rec = ttk.LabelFrame(side, text="Recording", padding=6)
        rec.pack(fill="both", expand=True, pady=(8, 0))
        self.rec_var = tk.BooleanVar(value=False)
        self.phone_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(rec, text="● Record", variable=self.rec_var, command=self._rec_toggled).pack(anchor="w")
        ttk.Checkbutton(rec, text="Record phone touches", variable=self.phone_var,
                        command=self._phone_toggled).pack(anchor="w")

        lf = ttk.Frame(rec)
        lf.pack(fill="both", expand=True, pady=4)
        self.steps_list = tk.Listbox(lf, width=40, height=18, activestyle="none", exportselection=False)
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.steps_list.yview)
        self.steps_list.configure(yscrollcommand=sb.set)
        self.steps_list.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        ed = ttk.Frame(rec)
        ed.pack(fill="x")
        for i, (label, cmd) in enumerate([("▲", lambda: self._move(-1)), ("▼", lambda: self._move(1)),
                                          ("Delete", self._delete), ("+ Wait 1s", self._add_wait),
                                          ("Clear", self._clear)]):
            ttk.Button(ed, text=label, width=7 if len(label) > 2 else 3, command=cmd).grid(row=0, column=i, padx=1)

        io = ttk.Frame(rec)
        io.pack(fill="x", pady=(6, 0))
        self.speed_var = tk.DoubleVar(value=1.0)
        ttk.Button(io, text="▶ Replay", command=self._replay).pack(side="left")
        ttk.Label(io, text=" speed").pack(side="left")
        ttk.Spinbox(io, from_=0.25, to=8, increment=0.25, width=5, textvariable=self.speed_var).pack(side="left")
        ttk.Button(io, text="Save…", command=self._save).pack(side="right")
        ttk.Button(io, text="Load…", command=lambda: self._load()).pack(side="right")

        self.status = ttk.Label(self.root, text="", anchor="w", padding=(8, 2))
        self.status.grid(row=1, column=0, columnspan=2, sticky="ew")

        c = self.canvas
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        # Right button is <Button-2> on macOS (aqua), <Button-3> elsewhere.
        right = "<Button-2>" if self.root.tk.call("tk", "windowingsystem") == "aqua" else "<Button-3>"
        c.bind(right, lambda e: self.send_key("BACK"))
        c.bind("<MouseWheel>", self._on_wheel)                       # macOS / Windows
        c.bind("<Button-4>", lambda e: self._on_wheel(e, 120))      # X11
        c.bind("<Button-5>", lambda e: self._on_wheel(e, -120))
        c.bind("<Motion>", self._on_motion)

    # ============================================================ capture
    def _capture_loop(self) -> None:
        last_orient_check = 0.0
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                frame = None
                if self._raw_ok:
                    try:
                        frame = Device.rgba_to_ppm(*self.d.screencap_raw())
                    except Exception:
                        self._raw_ok = False          # fall back to PNG permanently
                if frame is None:
                    frame = self.d.exec_out("screencap -p")
                if frame[:2] == b"P6" or frame.startswith(b"\x89PNG"):
                    self._frame, self._frame_id = frame, self._frame_id + 1
                    dt = time.monotonic() - t0
                    inst = 1 / dt if dt else 0
                    self.fps = inst if self.fps == 0 else 0.7 * self.fps + 0.3 * inst
            except Exception as exc:  # device unplugged etc.
                self._set_status(f"capture error: {exc}")
                time.sleep(1)
            if time.monotonic() - last_orient_check > 3:
                last_orient_check = time.monotonic()
                try:
                    self._orientation = self.d.orientation()
                    if self.listener:
                        self.listener.set_orientation(self._orientation)
                except Exception:
                    pass
            self._kick.wait(self.interval)
            self._kick.clear()

    def _call(self, fn, *args) -> None:
        """Schedule fn(*args) on the Tk thread (safe to call from any thread)."""
        self._calls.put((fn, args))

    def _tick(self) -> None:
        while True:
            try:
                fn, args = self._calls.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args)
            except Exception as exc:
                self.status.configure(text=f"error: {exc}")
        if self._frame_id != self._shown_id and self._frame is not None:
            self._shown_id = self._frame_id
            self._show(self._frame)
        self.root.after(30, self._tick)

    def _show(self, frame: bytes) -> None:
        if frame[:2] == b"P6":
            full = tk.PhotoImage(data=frame, format="ppm")
        else:
            full = tk.PhotoImage(data=base64.b64encode(frame))
        w, h = full.width(), full.height()
        if (w, h) != (self.dev_w, self.dev_h) or self.photo is None:
            self.dev_w, self.dev_h = w, h          # rotation / first frame
            self.factor = self.scale or max(1, math.ceil(h / (self.root.winfo_screenheight() - 160)))
            self.canvas.configure(width=w // self.factor, height=h // self.factor)
        self.photo = full.subsample(self.factor) if self.factor > 1 else full
        self.canvas.delete("frame", "placeholder")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw", tags="frame")
        self.canvas.tag_lower("frame")

    # ============================================================== input
    def _dev(self, e) -> tuple[int, int]:
        x = min(max(int(e.x * self.factor), 0), self.dev_w - 1)
        y = min(max(int(e.y * self.factor), 0), self.dev_h - 1)
        return x, y

    def _do(self, fn, *args) -> None:
        """Run an adb call on the ordered I/O worker, then refresh the mirror."""
        def job():
            try:
                fn(*args)
            except Exception as exc:
                self._set_status(f"error: {exc}")
            self._kick.set()
        self._io.submit(job)

    def _now(self) -> float:
        return time.monotonic() - self.rec_start

    def perform(self, step: Step) -> None:
        """Send a step to the phone and (if recording) record it."""
        self._record(step)
        rec = Recording(self.dev_w, self.dev_h, [step])
        self._do(lambda: rec.replay(self.d))

    def _on_press(self, e) -> None:
        self._press = (*self._dev(e), time.monotonic())
        self._press_t = self._now()

    def _on_drag(self, e) -> None:
        if not self._press:
            return
        x0, y0 = self._press[0] / self.factor, self._press[1] / self.factor
        if self._drag_line:
            self.canvas.coords(self._drag_line, x0, y0, e.x, e.y)
        else:
            self._drag_line = self.canvas.create_line(x0, y0, e.x, e.y, fill="#ff3b6b", width=3, arrow="last")

    def _on_release(self, e) -> None:
        if not self._press:
            return
        x0, y0, t0 = self._press
        self._press = None
        if self._drag_line:
            self.canvas.delete(self._drag_line)
            self._drag_line = None
        x1, y1 = self._dev(e)
        ms = int((time.monotonic() - t0) * 1000)
        self.perform(classify(x0, y0, x1, y1, ms, t=self._press_t, source="window"))

    def _on_wheel(self, e, delta: int | None = None) -> None:
        d = delta if delta is not None else e.delta
        # macOS reports small deltas (±1..10), Windows multiples of 120
        self._wheel_accum += (1 if d > 0 else -1) * (1 if abs(d) < 120 else abs(d) // 120)
        if self._wheel_job:
            self.root.after_cancel(self._wheel_job)
        self._wheel_job = self.root.after(200, self._flush_wheel)

    def _flush_wheel(self) -> None:
        n, self._wheel_accum, self._wheel_job = self._wheel_accum, 0, None
        if not n:
            return
        dist = min(abs(n) * 0.08, 0.6) * self.dev_h
        cx, cy = self.dev_w // 2, self.dev_h // 2
        y0, y1 = (cy - dist / 2, cy + dist / 2) if n > 0 else (cy + dist / 2, cy - dist / 2)
        self.perform(Step("swipe", cx, int(y0), cx, int(y1), duration_ms=250, t=self._now()))

    def _on_motion(self, e) -> None:
        x, y = self._dev(e)
        self._set_status(f"({x}, {y})  [{x / self.dev_w:.3f}, {y / self.dev_h:.3f}]   "
                         f"{self.fps:.1f} fps   {len(self.recording.steps)} steps"
                         + ("   ● REC" if self.rec_var.get() else ""))

    def send_key(self, key: str) -> None:
        self.perform(Step("key", key=key, t=self._now()))

    def send_text(self) -> None:
        value = self.text_var.get()
        if value:
            self.perform(Step("text", text=value, t=self._now()))
            self.text_var.set("")

    # ======================================================== phone touches
    def _phone_toggled(self) -> None:
        if self.phone_var.get():
            from .phone_input import PhoneInputListener
            try:
                self.listener = PhoneInputListener(
                    self.d,
                    on_step=lambda s: self._call(self._phone_step, s),
                    clock=self._now,
                    on_touch=lambda x, y, down: self._call(self._show_touch, x, y, down),
                    on_ignored=lambda msg: self._set_status(msg),
                )
                self.listener.start()
                names = ", ".join(t.name for t in self.listener.touch_devices)
                self._set_status(f"listening for phone touches on {names}")
            except Exception as exc:
                self.phone_var.set(False)
                messagebox.showerror("Phone touches", str(exc))
        elif self.listener:
            self.listener.stop()
            self.listener = None

    def _phone_step(self, step: Step) -> None:
        self._record(step)
        self._kick.set()

    def _show_touch(self, x: int, y: int, down: bool) -> None:
        if self._touch_marker:
            self.canvas.delete(self._touch_marker)
            self._touch_marker = None
        if down:
            cx, cy, r = x / self.factor, y / self.factor, 10
            self._touch_marker = self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                                         outline="#3bd1ff", width=3)

    # ========================================================== recording
    def _rec_toggled(self) -> None:
        if self.rec_var.get() and not self.recording.steps:
            self.rec_start = time.monotonic()
            self.recording.width, self.recording.height = self.dev_w, self.dev_h

    def _record(self, step: Step) -> None:
        if not self.rec_var.get() or self.replaying:
            return
        self.recording.steps.append(step)
        tag = "📱 " if step.source == "phone" else ""
        self.steps_list.insert("end", f"{len(self.recording.steps):>3}. {tag}{step.describe()}")
        self.steps_list.see("end")

    def _refresh_list(self, select: int | None = None) -> None:
        self.steps_list.delete(0, "end")
        for i, s in enumerate(self.recording.steps, 1):
            tag = "📱 " if s.source == "phone" else ""
            self.steps_list.insert("end", f"{i:>3}. {tag}{s.describe()}")
        if select is not None and 0 <= select < len(self.recording.steps):
            self.steps_list.selection_set(select)
            self.steps_list.see(select)

    def _selected(self) -> int | None:
        sel = self.steps_list.curselection()
        return sel[0] if sel else None

    def _move(self, delta: int) -> None:
        i = self._selected()
        steps = self.recording.steps
        if i is None or not (0 <= i + delta < len(steps)):
            return
        steps[i], steps[i + delta] = steps[i + delta], steps[i]
        steps[i].t, steps[i + delta].t = steps[i + delta].t, steps[i].t   # keep timeline monotonic
        self._refresh_list(i + delta)

    def _delete(self) -> None:
        i = self._selected()
        if i is not None:
            del self.recording.steps[i]
            self._refresh_list(min(i, len(self.recording.steps) - 1))

    def _add_wait(self) -> None:
        i = self._selected()
        steps = self.recording.steps
        pos = len(steps) if i is None else i + 1
        t = steps[pos - 1].t if pos else 0.0
        steps.insert(pos, Step("wait", duration_ms=1000, t=t))
        self._refresh_list(pos)

    def _clear(self) -> None:
        if self.recording.steps and messagebox.askyesno("Clear", "Delete all recorded steps?"):
            self.recording.steps.clear()
            self._refresh_list()

    def _replay(self) -> None:
        if not self.recording.steps or self.replaying:
            return
        self.replaying = True
        speed = max(float(self.speed_var.get() or 1), 0.05)

        def highlight(i, _step):
            self._call(lambda: (self.steps_list.selection_clear(0, "end"),
                                self.steps_list.selection_set(i), self.steps_list.see(i)))

        def job():
            try:
                self.recording.replay(self.d, speed=speed, on_step=highlight)
                self._set_status("replay finished")
            except Exception as exc:
                self._set_status(f"replay error: {exc}")
            finally:
                self.replaying = False
                self._kick.set()
        self._set_status("replaying...")
        self._io.submit(job)

    def _save(self) -> None:
        if not self.recording.steps:
            return
        REC_DIR.mkdir(parents=True, exist_ok=True)
        path = filedialog.asksaveasfilename(initialdir=REC_DIR, defaultextension=".json",
                                            initialfile=time.strftime("rec_%Y%m%d_%H%M%S.json"),
                                            filetypes=[("Recording", "*.json")])
        if not path:
            return
        path = Path(path)
        self.recording.save(path)
        path.with_suffix(".py").write_text(self.recording.to_python())
        self._set_status(f"saved {path} and {path.with_suffix('.py').name}")

    def _load(self, path: Path | None = None) -> None:
        if path is None:
            p = filedialog.askopenfilename(initialdir=REC_DIR, filetypes=[("Recording", "*.json")])
            if not p:
                return
            path = Path(p)
        self.recording = Recording.load(path)
        self._refresh_list()
        self._set_status(f"loaded {path} ({len(self.recording.steps)} steps)")

    # ============================================================== misc
    def _set_status(self, text: str) -> None:
        self._call(self.status.configure, {"text": text})

    def close(self) -> None:
        self._stop.set()
        self._kick.set()
        if self.listener:
            self.listener.stop()
        self._io.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def run_mirror(device: Device, scale: int | None = None, interval: float = 0.0,
               load: str | None = None) -> None:
    MirrorApp(device, scale=scale, interval=interval, load=load).run()
