"""
tray.py - Cross-platform tray / menu-bar app for the Claude Code usage dashboard.

Shows today's and this month's Claude spend in the system tray / menu bar.
Polls the running dashboard server (http://localhost:8080 by default) every
60 seconds for fresh numbers.

Platform-specific backend:
  - macOS  -> rumps (light, native NSStatusBar app)
  - Linux/Windows -> pystray + PIL (cross-platform indicator)

Both backends are OPTIONAL dependencies. They are imported lazily inside
`run()` so that:
  - `import tray` always succeeds (used by tests, by `cli.py`)
  - missing deps produce a friendly install hint rather than a crash

Launch:
    python cli.py tray
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from datetime import date

# Importing from the repo root works because cli.py / tests run from there.
from pricing import calc_cost


# ── Config ────────────────────────────────────────────────────────────────────

DASHBOARD_URL = "http://localhost:8080"
HEALTH_ENDPOINT = "/api/health"
DATA_ENDPOINT = "/api/data"
POLL_INTERVAL_SECONDS = 60
HTTP_TIMEOUT_SECONDS = 5

# Cost thresholds (USD) used for the optional badge colouring.
# < GREEN_MAX        -> green   (cheap day)
# GREEN_MAX..AMBER_MAX -> amber (medium)
# > AMBER_MAX        -> red    (heavy)
GREEN_MAX = 1.0
AMBER_MAX = 10.0


# ── Pure helpers (covered by tests) ───────────────────────────────────────────

def fmt_money(amount: float) -> str:
    """Format a dollar amount as $X.XX (always two decimals, never negative)."""
    try:
        n = float(amount or 0.0)
    except (TypeError, ValueError):
        n = 0.0
    if n < 0:
        n = 0.0
    return f"${n:.2f}"


def cost_color(amount: float) -> str:
    """Map a dollar amount to one of 'green' / 'amber' / 'red'.

    Boundaries: green < $1, amber $1..$10 inclusive, red > $10.
    """
    try:
        n = float(amount or 0.0)
    except (TypeError, ValueError):
        n = 0.0
    if n < GREEN_MAX:
        return "green"
    if n <= AMBER_MAX:
        return "amber"
    return "red"


def sum_costs_for_days(daily_by_model, day_predicate) -> float:
    """Sum costs across all (day, model) rows whose `day` matches `day_predicate`.

    `daily_by_model` is the list returned by /api/data; each entry has
    'day', 'model', 'input', 'output', 'cache_read', 'cache_creation',
    optionally 'cache_1h'.
    """
    total = 0.0
    for row in daily_by_model or ():
        day = row.get("day") or ""
        if not day_predicate(day):
            continue
        total += calc_cost(
            row.get("model"),
            row.get("input") or 0,
            row.get("output") or 0,
            row.get("cache_read") or 0,
            row.get("cache_creation") or 0,
            row.get("cache_1h") or 0,
        )
    return total


def compute_today_and_month(data, today_iso: str | None = None) -> tuple[float, float]:
    """Return (today_cost, month_cost) given the /api/data payload.

    `today_iso` is injectable for tests; defaults to today's date.
    Unknown / missing data yields (0.0, 0.0).
    """
    if not isinstance(data, dict):
        return 0.0, 0.0
    if today_iso is None:
        today_iso = date.today().isoformat()
    month_prefix = today_iso[:7]  # 'YYYY-MM'

    daily = data.get("daily_by_model") or []
    today_cost = sum_costs_for_days(daily, lambda d: d == today_iso)
    month_cost = sum_costs_for_days(daily, lambda d: d.startswith(month_prefix))
    return today_cost, month_cost


# ── Dashboard polling ─────────────────────────────────────────────────────────

def fetch_json(url: str, timeout: float = HTTP_TIMEOUT_SECONDS):
    """GET `url` and decode JSON. Returns None on any error."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError, ValueError):
        return None


def poll_once(base_url: str = DASHBOARD_URL) -> dict:
    """Hit /api/health then /api/data; return a small status dict.

    {'ok': bool, 'today': float, 'month': float, 'error': str | None}
    """
    health = fetch_json(base_url + HEALTH_ENDPOINT)
    if not health:
        return {"ok": False, "today": 0.0, "month": 0.0,
                "error": "dashboard unreachable"}
    if health.get("status") not in ("ok", "no-db"):
        return {"ok": False, "today": 0.0, "month": 0.0,
                "error": str(health.get("error") or health.get("status") or "unknown")}

    data = fetch_json(base_url + DATA_ENDPOINT)
    if not data or "error" in data:
        return {"ok": False, "today": 0.0, "month": 0.0,
                "error": (data or {}).get("error") or "no data"}

    today, month = compute_today_and_month(data)
    return {"ok": True, "today": today, "month": month, "error": None}


def open_dashboard(base_url: str = DASHBOARD_URL) -> None:
    """Open the dashboard in the user's default browser."""
    webbrowser.open(base_url)


# ── Backend selection ─────────────────────────────────────────────────────────

def _pick_backend():
    """Return 'rumps', 'pystray', or None depending on what's installed."""
    if sys.platform == "darwin":
        try:
            import rumps  # noqa: F401
            return "rumps"
        except ImportError:
            pass
    try:
        import pystray  # noqa: F401
        import PIL  # noqa: F401
        return "pystray"
    except ImportError:
        return None


# ── rumps backend (macOS) ─────────────────────────────────────────────────────

def _run_rumps(base_url: str) -> None:
    import rumps

    class TrayApp(rumps.App):
        def __init__(self):
            super().__init__("$--.--", quit_button=None)
            self.today_item = rumps.MenuItem("Today: $--.--")
            self.month_item = rumps.MenuItem("This month: $--.--")
            self.status_item = rumps.MenuItem("Connecting...")
            self.status_item.set_callback(None)
            self.menu = [
                self.today_item,
                self.month_item,
                None,
                self.status_item,
                None,
                rumps.MenuItem("Open Dashboard", callback=lambda _: open_dashboard(base_url)),
                rumps.MenuItem("Quit", callback=rumps.quit_application),
            ]
            self._timer = rumps.Timer(self._tick, POLL_INTERVAL_SECONDS)
            self._timer.start()
            # Initial fetch on a background thread so the UI shows immediately.
            threading.Thread(target=self._tick, args=(None,), daemon=True).start()

        def _tick(self, _sender):
            status = poll_once(base_url)
            if status["ok"]:
                self.title = fmt_money(status["today"])
                self.today_item.title = f"Today: {fmt_money(status['today'])}"
                self.month_item.title = f"This month: {fmt_money(status['month'])}"
                self.status_item.title = f"Status: {cost_color(status['today'])}"
            else:
                self.title = "$--.--"
                self.status_item.title = f"Offline ({status['error']})"

    TrayApp().run()


# ── pystray backend (Linux / Windows / macOS fallback) ────────────────────────

def _make_icon_image(color: str):
    """Build a tiny 64x64 PNG icon coloured by status."""
    from PIL import Image, ImageDraw
    palette = {"green": (60, 180, 75), "amber": (230, 160, 30), "red": (200, 50, 50)}
    rgb = palette.get(color, (140, 140, 140))
    img = Image.new("RGB", (64, 64), (32, 32, 32))
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, 56, 56), fill=rgb)
    return img


def _run_pystray(base_url: str) -> None:
    import pystray

    state = {"today": 0.0, "month": 0.0, "ok": False, "error": "connecting"}

    def make_menu():
        today_label = f"Today: {fmt_money(state['today'])}"
        month_label = f"This month: {fmt_money(state['month'])}"
        status_label = (f"Status: {cost_color(state['today'])}"
                        if state["ok"] else f"Offline ({state['error']})")
        return pystray.Menu(
            pystray.MenuItem(today_label, None, enabled=False),
            pystray.MenuItem(month_label, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(status_label, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open Dashboard", lambda icon, item: open_dashboard(base_url)),
            pystray.MenuItem("Quit", lambda icon, item: icon.stop()),
        )

    icon = pystray.Icon(
        "claude-usage",
        icon=_make_icon_image("green"),
        title="Claude Usage",
        menu=make_menu(),
    )

    def poll_loop():
        import time
        while True:
            status = poll_once(base_url)
            state.update(status)
            try:
                icon.icon = _make_icon_image(
                    cost_color(status["today"]) if status["ok"] else "red"
                )
                icon.title = (f"Claude Today: {fmt_money(status['today'])}"
                              if status["ok"] else "Claude (offline)")
                icon.menu = make_menu()
            except Exception:
                pass
            time.sleep(POLL_INTERVAL_SECONDS)

    threading.Thread(target=poll_loop, daemon=True).start()
    icon.run()


# ── Public entry point ────────────────────────────────────────────────────────

INSTALL_HINT = (
    "Tray app requires an optional dependency.\n"
    "  macOS:           pip install rumps\n"
    "  Linux / Windows: pip install pystray pillow\n"
)


def run(base_url: str = DASHBOARD_URL) -> int:
    """Launch the tray app. Returns a process exit code."""
    backend = _pick_backend()
    if backend is None:
        sys.stderr.write(INSTALL_HINT)
        return 1
    if backend == "rumps":
        _run_rumps(base_url)
    else:
        _run_pystray(base_url)
    return 0


if __name__ == "__main__":
    sys.exit(run())
