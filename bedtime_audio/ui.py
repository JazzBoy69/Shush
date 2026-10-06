"""Full-screen native graphical UI for the Raspberry Pi bedtime player."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import font
from typing import TYPE_CHECKING, Callable

from .planner import Phase, StartChoice

if TYPE_CHECKING:
    from .service import BedtimeService


LOG = logging.getLogger(__name__)
TICK_MS = 100
COLORS = {
    # Keep the overall luminance low for a room used just before sleep.
    "background": "#100c0a",
    "panel": "#1d1713",
    "text": "#d8c3a8",
    "muted": "#927b65",
    "accent": "#60452f",
    "button": "#2b211a",
    "button_active": "#3a2b20",
    "stop": "#38221f",
    "error": "#bd8873",
    "blackout": "#000000",
}


def run_ui(service: BedtimeService) -> None:
    """Show a full-screen UI; retain remote playback when no display is present."""
    try:
        root = tk.Tk()
    except tk.TclError as error:
        LOG.warning("Graphical display unavailable; running controls headless: %s", error)
        while not service.stop_requested.is_set():
            service.service_tick()
            service.stop_requested.wait(TICK_MS / 1000)
        return

    app = BedtimeWindow(root, service)
    app.run()


class BedtimeWindow:
    def __init__(self, root: tk.Tk, service: BedtimeService) -> None:
        self.root = root
        self.service = service
        self.root.title("Shush")
        self.root.configure(bg=COLORS["background"])
        self.root.attributes("-fullscreen", True)
        self.root.overrideredirect(True)
        self.root.geometry(f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0")
        self.root.protocol("WM_DELETE_WINDOW", self._stop_playback)

        # Scale with both dimensions so controls stay comfortably large on a
        # living-room display without overflowing a short or portrait screen.
        scale = max(
            0.8,
            min(root.winfo_screenheight() / 700, root.winfo_screenwidth() / 1120, 2.4),
        )
        self.scale = scale
        self.title_font = font.Font(family="DejaVu Sans", size=round(20 * scale), weight="bold")
        self.time_font = font.Font(family="DejaVu Sans", size=round(80 * scale), weight="bold")
        self.body_font = font.Font(family="DejaVu Sans", size=round(14 * scale))
        self.button_font = font.Font(family="DejaVu Sans", size=round(16 * scale), weight="bold")

        card = tk.Frame(root, bg=COLORS["panel"], padx=round(76 * scale), pady=round(52 * scale))
        card.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(card, text="SHUSH", font=self.title_font, fg=COLORS["muted"], bg=COLORS["panel"]).pack()
        tk.Label(card, text="WAKE AT", font=self.body_font, fg=COLORS["muted"], bg=COLORS["panel"]).pack(pady=(round(22 * scale), 0))
        self.time_label = tk.Label(card, font=self.time_font, fg=COLORS["text"], bg=COLORS["panel"])
        self.time_label.pack(pady=(0, 4))
        tk.Label(
            card,
            text="Set time:  ← / →  15 min     ·     ↑ / ↓  1 hour",
            font=self.body_font,
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).pack(pady=(0, 18))

        self.mode_button = self._button(card, "", self._toggle_mode, accent=True)
        self.mode_button.pack(fill="x", pady=(round(8 * scale), round(8 * scale)))
        tk.Label(
            card,
            text="CHANNEL  ▲ / ▼  changes selection",
            font=self.body_font,
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).pack(pady=(0, 12))

        play_row = tk.Frame(card, bg=COLORS["panel"])
        play_row.pack(fill="x")
        self.play_button = self._button(play_row, "▶  PLAY", self._start, accent=True)
        self.play_button.pack(side="left", expand=True, fill="x", padx=(0, 7))
        self.pause_button = self._button(play_row, "Ⅱ  PLAY / PAUSE", self._toggle_pause)
        self.pause_button.pack(side="left", expand=True, fill="x", padx=7)
        self.stop_button = self._button(play_row, "■  STOP", self._stop_playback, stop=True)
        self.stop_button.pack(side="left", expand=True, fill="x", padx=(7, 0))

        self.status_label = tk.Label(
            card,
            text="",
            font=self.body_font,
            fg=COLORS["text"],
            bg=COLORS["panel"],
        )
        self.status_label.pack(pady=(18, 0))
        self.error_label = tk.Label(
            card,
            text="",
            font=self.body_font,
            fg=COLORS["error"],
            bg=COLORS["panel"],
            wraplength=round(760 * scale),
        )
        self.error_label.pack(pady=(8, 0))

        # Keep the room dark once playback begins. The service and evdev
        # listener continue running underneath this full-screen overlay.
        self.blackout = tk.Frame(root, bg=COLORS["blackout"], borderwidth=0)
        self.blackout.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.blackout.lower()
        self._refresh()

    def run(self) -> None:
        self.root.after(TICK_MS, self._tick)
        self.root.mainloop()

    def _button(
        self,
        parent: tk.Misc,
        label: str,
        command: Callable[[], None],
        *,
        accent: bool = False,
        stop: bool = False,
    ) -> tk.Button:
        background = COLORS["stop"] if stop else COLORS["button"]
        foreground = COLORS["text"]
        if accent:
            background = COLORS["accent"]
            foreground = COLORS["background"]
        return tk.Button(
            parent,
            text=label,
            command=command,
            font=self.button_font,
            fg=foreground,
            bg=background,
            activeforeground=foreground,
            activebackground=COLORS["button_active"],
            relief="flat",
            borderwidth=0,
            padx=round(28 * self.scale),
            pady=round(20 * self.scale),
            cursor="hand2",
            takefocus=False,
        )

    def _tick(self) -> None:
        if self.service.stop_requested.is_set():
            self.root.destroy()
            return
        self.service.service_tick()
        self._refresh()
        self.root.after(TICK_MS, self._tick)

    def _refresh(self) -> None:
        with self.service.state_lock:
            phase = self.service.session.phase
            paused = self.service.paused
            alarm = f"{self.service.alarm_minutes // 60:02d}:{self.service.alarm_minutes % 60:02d}"
            mode = self.service.selected_choice
            error = self.service.last_ui_error

        self.time_label.configure(text=alarm)
        self.mode_button.configure(
            text="Alarm with playlist" if mode is StartChoice.READINGS else "Alarm without playlist"
        )
        active = phase in (Phase.PLAYLIST, Phase.PINK_NOISE)
        if active:
            self.blackout.lift()
        else:
            self.blackout.lower()
        self.mode_button.configure(state=tk.DISABLED if active else tk.NORMAL)
        if active:
            if paused:
                status = "Paused"
            elif phase is Phase.PLAYLIST:
                status = "Playing today's reading playlist"
            else:
                status = "Playing pink noise"
        elif phase is Phase.STOPPED:
            status = "Ready"
        else:
            status = "Ready · press Play to begin"
        self.status_label.configure(text=status)
        self.pause_button.configure(text="▶  RESUME" if paused else "Ⅱ  PLAY / PAUSE")
        self.pause_button.configure(state=tk.NORMAL if active else tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL if active else tk.DISABLED)
        self.play_button.configure(state=tk.DISABLED if active else tk.NORMAL)
        self.error_label.configure(text=error or "")

    def _toggle_mode(self) -> None:
        self.service.handle_media_key("mode")
        self._refresh()

    def _start(self) -> None:
        self.service.handle_media_key("play")
        self._refresh()

    def _toggle_pause(self) -> None:
        self.service.handle_media_key("toggle")
        self._refresh()

    def _stop_playback(self) -> None:
        self.service.handle_media_key("stop")
        self._refresh()
