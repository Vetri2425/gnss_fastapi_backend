#!/usr/bin/env python3
"""
OLED Display - DYX_BASE
Startup: Boot Splash -> Loading Bar
Loop:    Stage screen (during autoflow) -> Caster -> GNSS -> 4G (after COMPLETE)
Robust:  API calls run in background threads -- display never freezes
"""

import time
import threading
import urllib.request
import json
import subprocess
from PIL import ImageFont
from luma.core.interface.serial import spi
from luma.oled.device import ssd1306
from luma.core.render import canvas

# -- Init -----------------------------------------------------------------------
# Device initialization deferred to main() to support testing without hardware
serial = None
device = None
W, H = 128, 64  # Default SSD1306 dimensions

# -- Shared state (updated by background fetcher thread) ------------------------
_state = {
    "4g_ip":         None,
    "4g_signal":     None,   # int 0-5, or None
    "position":      {},     # From /api/v1/status.position
    "survey":        {},     # From /api/v1/status.survey
    "rtcm":          {},     # From /api/v1/status.rtcm
    "ntrip":         {},     # From /api/v1/status.ntrip
    "autoflow":      {},     # From /api/v1/autoflow/status (state, enabled, last_error)
    "last_fetch":    0,
    "fetch_ok":      False,
}
_state_lock = threading.Lock()


# -- Background data fetcher (runs every 2s, never blocks display) --------------
def _fetch_once():
    """Fetch all required data from FastAPI backend in one shot"""

    # 4G IP
    try:
        out = subprocess.check_output(
            "ip -4 addr show ppp0 2>/dev/null | grep inet | awk '{print $2}' | cut -d/ -f1",
            shell=True, text=True, timeout=2).strip()
        ip = out or None
    except Exception:
        ip = None

    # ===== SINGLE API CALL: /api/v1/status (contains position, survey, rtcm, ntrip) =====
    full_status = {}
    try:
        r = urllib.request.urlopen("http://localhost:8000/api/v1/status", timeout=2)
        full_status = json.loads(r.read())
    except Exception as e:
        pass  # Silent fail - will use empty dicts below

    # Extract sub-fields from full_status
    position = full_status.get("position", {})
    survey = full_status.get("survey", {})
    rtcm = full_status.get("rtcm", {})
    ntrip = full_status.get("ntrip", {})

    # ===== SECOND API CALL: /api/v1/autoflow/status (state, enabled, error) =====
    autoflow = {}
    try:
        r = urllib.request.urlopen("http://localhost:8000/api/v1/autoflow/status", timeout=2)
        autoflow = json.loads(r.read())
    except Exception:
        pass

    # 4G signal strength (via mmcli, fall back to 4/5 when connected)
    sig = None
    try:
        out = subprocess.check_output(
            "mmcli -m 0 --simple-status 2>/dev/null | grep signal | grep -o '[0-9]*' | head -1",
            shell=True, text=True, timeout=3).strip()
        if out:
            pct = int(out)
            sig = round(pct / 20)  # 0-100 -> 0-5 bars
    except Exception:
        pass
    # If ppp0 has IP but mmcli unavailable, default to 4/5 bars
    if sig is None and ip:
        sig = 4

    with _state_lock:
        _state["4g_ip"]      = ip
        _state["4g_signal"]  = sig
        _state["position"]   = position
        _state["survey"]     = survey
        _state["rtcm"]       = rtcm
        _state["ntrip"]      = ntrip
        _state["autoflow"]   = autoflow
        _state["last_fetch"] = time.time()
        _state["fetch_ok"]   = True


def _fetcher_loop():
    while True:
        try:
            _fetch_once()
        except Exception:
            pass
        time.sleep(2)


def start_fetcher():
    t = threading.Thread(target=_fetcher_loop, daemon=True)
    t.start()


# -- Helpers -------------------------------------------------------------------
def get_state():
    with _state_lock:
        return dict(_state)

def fmt_bytes(b):
    b = int(b) if b else 0
    if b >= 1_000_000: return f"{b/1_000_000:.2f}MB"
    if b >= 1_000:     return f"{b/1_000:.1f}KB"
    return f"{b}B"

def fmt_uptime(s):
    s = int(s) if s else 0
    if s >= 3600: return f"{s//3600}h{(s%3600)//60}m"
    if s >= 60:   return f"{s//60}m{s%60}s"
    return f"{s}s"

def page_header(draw, title):
    draw.text((2, 2), title, fill="white")
    draw.line([(0, 13), (W, 13)], fill="white")


# -- Startup animations --------------------------------------------------------
def anim_boot_splash():
    # Logo reveal — full border, DYX_BASE centred (large font)
    font_large = ImageFont.truetype("/usr/share/fonts/truetype/ubuntu/UbuntuMono-B.ttf", 12)
    for frame in range(28):
        t = frame / 27
        with canvas(device) as draw:
            draw.rectangle([0, 0, W - 1, H - 1], outline="white")
            if t > 0.30:
                draw.text((28, 24), "DYX_BASE", font=font_large, fill="white")
            if t > 0.80 and frame % 4 < 2:
                draw.text((76, 54), "ONLINE", fill="white")
        time.sleep(0.08)
    time.sleep(2.0)


def anim_loading_bar():
    SEG = 14                       # shorter bar to leave room for % beside it
    SEG_W, SEG_GAP = 5, 1
    BAR_X, BAR_Y = 4, 30
    BAR_END = BAR_X + SEG * (SEG_W + SEG_GAP)   # x where bar ends
    for i in range(41):
        pct    = int(i / 40 * 100)
        filled = int(SEG * i / 40)
        with canvas(device) as draw:
            draw.rectangle([0, 0, W - 1, H - 1], outline="white")
            draw.text((34, 6), "DYX_BASE", fill="white")
            for s in range(SEG):
                sx = BAR_X + s * (SEG_W + SEG_GAP)
                if s < filled:
                    draw.rectangle([sx, BAR_Y, sx + SEG_W - 1, BAR_Y + 8], fill="white")
                else:
                    draw.rectangle([sx, BAR_Y, sx + SEG_W - 1, BAR_Y + 8], outline="white")
            draw.text((BAR_END + 4, BAR_Y), f"{pct}%", fill="white")
        time.sleep(0.07)
    time.sleep(0.5)


def show_page(draw_fn, duration=4):
    deadline = time.time() + duration
    while time.time() < deadline:
        st = get_state()
        try:
            with canvas(device) as draw:
                draw_fn(draw, st)
        except Exception:
            pass
        time.sleep(0.25)


# -- Screen: Autoflow stage (SURVEYING / STREAMING / ERROR / etc) ------------------
def draw_autoflow_stage(draw, st):
    af     = st.get("autoflow", {})
    state  = af.get("state", "IDLE")  # IMPORTANT: API uses "state" not "stage"
    survey = st.get("survey", {})
    hacc   = survey.get("mean_accuracy", 0) or 0
    obs    = survey.get("observation_time", 0) or 0
    err    = af.get("last_error")  # IMPORTANT: API uses "last_error" not "error"

    draw.rectangle([0, 0, W - 1, H - 1], outline="white")
    # Header
    draw.text((2, 2), "DYX_BASE", fill="white")
    draw.text((90, 2), "AUTO", fill="white")
    draw.line([(0, 13), (W, 13)], fill="white")

    # Stage label
    draw.text((2, 16), "STATE", fill="white")
    draw.text((2, 26), state, fill="white")

    draw.line([(0, 38), (W, 38)], fill="white")

    # State-specific bottom row
    if state == "SURVEY":
        acc_txt = f"ACC {hacc:.3f}m" if hacc else "ACC --"
        obs_txt = f"OBS {obs}"
        draw.text((2, 41), acc_txt, fill="white")
        draw.text((80, 41), obs_txt, fill="white")
    elif state == "LOCKING":
        draw.text((2, 41), "Waiting for lock...", fill="white")
    elif state == "STREAMING":
        ntrip = st.get("ntrip", {})
        rate  = ntrip.get("data_rate_bps", 0) or 0
        draw.text((2, 41), f"RTCM: {rate} bps", fill="white")
    elif state == "ERROR":
        msg = (err or "Unknown error")[:20]
        draw.text((2, 41), msg, fill="white")
    elif state == "COMPLETE":
        draw.text((2, 41), "Streaming active", fill="white")
    else:
        draw.text((2, 41), f"Waiting ({state})...", fill="white")


# -- Screen: GNSS -- satellite icon + live count -------------------------------
def _draw_sat_icon(draw, cx, cy):
    """Pixel-art satellite icon centred at (cx, cy)."""
    # Body
    draw.rectangle([cx - 2, cy - 2, cx + 2, cy + 2], fill="white")
    # Left solar panel
    draw.rectangle([cx - 12, cy - 1, cx - 4, cy + 1], fill="white")
    # Right solar panel
    draw.rectangle([cx + 4, cy - 1, cx + 12, cy + 1], fill="white")
    # Mast below body
    draw.line([(cx, cy + 2), (cx, cy + 6)], fill="white")
    # Dish (triangle approximation)
    draw.line([(cx - 3, cy + 9), (cx, cy + 6)], fill="white")
    draw.line([(cx + 3, cy + 9), (cx, cy + 6)], fill="white")
    draw.line([(cx - 3, cy + 9), (cx + 3, cy + 9)], fill="white")


def draw_gnss(draw, st):
    data = st.get("position", {})
    fix  = data.get("fix_type_str", "no_fix") or "no_fix"  # IMPORTANT: API uses fix_type_str
    sats = data.get("num_satellites", 0) or 0
    hacc = data.get("accuracy", 0) or 0
    alt  = data.get("altitude", 0) or 0

    draw.rectangle([0, 0, W - 1, H - 1], outline="white")
    # Header
    draw.text((2, 2), "GNSS", fill="white")
    fix_short = "TIME" if "time" in fix.lower() else ("FIX" if fix != "no_fix" else "NO FIX")
    draw.text((80, 2), fix_short, fill="white")
    draw.line([(0, 13), (W, 13)], fill="white")

    # Left: satellite icon
    _draw_sat_icon(draw, 28, 32)

    # Right: large satellite count (bold trick - draw twice)
    count_str = str(sats)
    draw.text((74, 18), count_str, fill="white")
    draw.text((75, 18), count_str, fill="white")
    draw.text((74, 32), "SATS", fill="white")

    draw.line([(0, 50), (W, 50)], fill="white")

    # Bottom row: accuracy + altitude
    hacc_txt = f"hAcc {hacc:.3f}m" if hacc else "hAcc --"
    alt_txt  = f"{alt:.1f}m" if alt else ""
    draw.text((2, 53), hacc_txt, fill="white")
    if alt_txt:
        draw.text((90, 53), alt_txt, fill="white")


# -- Screen: 4G -- signal bars -------------------------------------------------
def _draw_signal_bars_inline(draw, x, y, level, max_bars=5):
    """Draw compact signal bars inline (small, side by side). level = 0..max_bars."""
    bar_w   = 5
    bar_gap = 2
    max_h   = 10
    for i in range(max_bars):
        bx    = x + i * (bar_w + bar_gap)
        bar_h = int(max_h * (i + 1) / max_bars)
        by    = y + max_h - bar_h
        if i < level:
            draw.rectangle([bx, by, bx + bar_w - 1, y + max_h], fill="white")
        else:
            draw.rectangle([bx, by, bx + bar_w - 1, y + max_h], outline="white")


def draw_4g(draw, st):
    ip  = st.get("4g_ip")
    sig = st.get("4g_signal")
    level = sig if sig is not None else (4 if ip else 0)

    draw.rectangle([0, 0, W - 1, H - 1], outline="white")

    # Row 1: "4G LTE" label + signal bars inline + signal count
    draw.text((2, 2), "4G LTE", fill="white")
    _draw_signal_bars_inline(draw, x=52, y=2, level=level)
    draw.text((96, 2), f"{level}/5", fill="white")

    draw.line([(0, 16), (W, 16)], fill="white")

    # Row 2: STATUS label left, value right on same line
    status_txt = "ONLINE" if ip else "OFFLINE"
    draw.text((4, 26), "STATUS", fill="white")
    draw.text((62, 26), status_txt, fill="white")

    # Row 3: IP label left, address right on same line
    draw.text((4, 42), "IP", fill="white")
    draw.text((22, 42), ip if ip else "No PPP", fill="white")


# -- Screen: NTRIP Caster ------------------------------------------------------
def draw_ntrip(draw, st):
    data      = st.get("ntrip", {})
    connected = data.get("connected", False)
    enabled   = data.get("enabled",   False)
    host      = data.get("host",      "---")
    mnt       = data.get("mountpoint","---")
    sent      = data.get("bytes_sent",     0) or 0
    rate      = data.get("data_rate_bps",  0) or 0
    uptime    = data.get("uptime",  0) or 0

    rate_txt = f"{rate/1000:.1f}K" if rate >= 1000 else f"{rate}b"

    draw.rectangle([0, 0, W - 1, H - 1], outline="white")

    # Header — CASTER bold (double-draw) + inverted LIVE badge
    draw.text((4, 3), "CASTER", fill="white")
    draw.text((5, 3), "CASTER", fill="white")
    if connected:
        draw.rectangle([94, 2, 124, 12], fill="white")
        draw.text((96, 3), "LIVE", fill="black")
    elif enabled:
        draw.text((94, 3), "CONN..", fill="white")
    else:
        draw.text((100, 3), "OFF", fill="white")

    # Host and mountpoint (no labels)
    draw.text((4, 16), host, fill="white")
    draw.text((4, 28), mnt,  fill="white")

    # Stats — labels (y=44) then values (y=53), three columns
    draw.text((4,  44), "SENT",             fill="white")
    draw.text((50, 44), "RATE",             fill="white")
    draw.text((98, 44), "UP",               fill="white")
    draw.text((4,  53), fmt_bytes(sent),    fill="white")
    draw.text((50, 53), rate_txt,           fill="white")
    draw.text((98, 53), fmt_uptime(uptime), fill="white")


# -- Main ----------------------------------------------------------------------
def main():
    global serial, device

    # Initialize hardware on startup
    print("Initializing OLED display hardware...")
    try:
        serial = spi(port=0, device=0, gpio_DC=24, gpio_RST=25)
        device = ssd1306(serial)
        print(f"✓ Display initialized: {device.width}×{device.height}")
    except Exception as e:
        print(f"✗ Display initialization failed: {e}")
        print("  (Make sure you're running on Raspberry Pi with SSD1306 connected)")
        return

    print("Starting background data fetcher...")
    start_fetcher()
    time.sleep(1)  # let first fetch complete

    print("Boot animations...")
    anim_boot_splash()
    anim_loading_bar()

    print("Status loop running (Ctrl+C to stop)...")
    last_4g_time   = 0
    FOUR_G_INTERVAL = 10   # show 4G every 10s in the post-COMPLETE cycle

    try:
        while True:
            st    = get_state()
            state = st.get("autoflow", {}).get("state", "IDLE")  # Use "state" not "stage"

            # Show autoflow stage until STREAMING or error
            if state not in ("STREAMING", "COMPLETE"):
                show_page(draw_autoflow_stage, duration=2)
            else:
                # After STREAMING: Caster (4s) -> GNSS (4s) -> 4G (10s, once per cycle)
                show_page(draw_ntrip, duration=4)
                show_page(draw_gnss,  duration=4)

                now = time.time()
                if now - last_4g_time >= FOUR_G_INTERVAL:
                    show_page(draw_4g, duration=10)
                    last_4g_time = time.time()

    except KeyboardInterrupt:
        print("\nStopped.")
        if device:
            device.clear()


if __name__ == "__main__":
    main()
