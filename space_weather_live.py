import os
import sys
import time
import math
import signal
import threading
import subprocess
import re
from io import BytesIO
from datetime import datetime, timezone

import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ============================================================
# SPACE WEATHER LIVE - NOAA + PYTHON + FFMPEG
# 1280x720 dashboard designed for YouTube LIVE
# ============================================================

W, H, FPS = 1280, 720, 30
REFRESH = 60
YOUTUBE_RTMP = os.getenv("YOUTUBE_RTMP", "rtmp://a.rtmp.youtube.com/live2")
YOUTUBE_KEY = os.getenv("YOUTUBE_KEY", "")

# Background music: comma/newline separated direct URLs.
# Example: https://.../music.mp3,https://.../music2.mp3
MUSIC_URLS = os.getenv("MUSIC_URLS", "").strip() or os.getenv("MUSIC_URL", "").strip()
MUSIC_DIR = "/tmp/space_weather_music"
MUSIC_PLAYLIST = os.path.join(MUSIC_DIR, "playlist.txt")
MUSIC_LOOP_FILE = os.path.join(MUSIC_DIR, "background_loop.m4a")

NOAA = "https://services.swpc.noaa.gov"

URL = {
    "kp": f"{NOAA}/json/planetary_k_index_1m.json",
    "wind": f"{NOAA}/json/rtsw/rtsw_wind_1m.json",
    "mag": f"{NOAA}/json/rtsw/rtsw_mag_1m.json",
    "xray": f"{NOAA}/json/goes/primary/xrays-1-day.json",
    "alerts": f"{NOAA}/products/alerts.json",
    "protons": f"{NOAA}/json/goes/primary/integral-protons-1-day.json",
    "electrons": f"{NOAA}/json/goes/primary/integral-electrons-1-day.json",
    "sunspots": f"{NOAA}/json/sunspot_report.json",
    "ovation": f"{NOAA}/json/ovation_aurora_latest.json",
}

# Real imagery sources (public, no API key required).
SDO_SUN_URL = "https://sdo.gsfc.nasa.gov/assets/img/latest/latest_1024_0171.jpg"
SDO_MAGNETOGRAM_URL = "https://sdo.gsfc.nasa.gov/assets/img/latest/latest_1024_HMIB.jpg"
SDO_CONTINUUM_URL = "https://sdo.gsfc.nasa.gov/assets/img/latest/latest_1024_HMIIC.jpg"
EPIC_LIST_URL = "https://epic.gsfc.nasa.gov/api/natural"
SOLAR_CYCLE_URL = f"{NOAA}/json/solar-cycle/observed-solar-cycle-indices.json"
IMAGE_REFRESH = 600  # seconds; real photos/history update far less often than NOAA data
CHANNEL_NAME = os.getenv("CHANNEL_NAME", "").strip() or "SPACE WEATHER LIVE"

running = True
lock = threading.Lock()
img_lock = threading.Lock()
REAL_IMAGES = {
    "sun": None, "earth": None, "magnetogram": None, "continuum": None,
    "sun_updated": None, "earth_updated": None,
}
HISTORY = {"months": [], "values": [], "current": None, "this_year_avg": None,
           "prev_year_avg": None, "prev_year_label": None}

def handle_signal(signum, frame):
    global running
    running = False
    print(f"[SHUTDOWN] Signal {signum} received", flush=True)

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)
DATA = {
    "kp": None, "speed": None, "density": None, "bz": None, "bt": None,
    "xray": None, "proton": None, "electron": None,
    "flare": "NO DATA", "alert": "NO CURRENT ALERTS",
    "updated": "Waiting for NOAA...", "history": [],
    "alert_time": None, "sunspot": None, "ovation_ok": False
}

session = requests.Session()
session.headers["User-Agent"] = "SpaceWeatherLive/2.0"

# Fonts
def font(size, bold=False):
    names = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for p in names:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

F_TITLE=font(30,True); F_H=font(17,True); F_B=font(31,True)
F_M=font(20,True); F_S=font(14); F_XS=font(11)
F_XXS=font(9)

def display_font(size):
    """Bundled condensed display face for the documentary-style header; falls back cleanly."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "BebasNeue.ttf")
    if os.path.exists(p):
        return ImageFont.truetype(p, size)
    return font(int(size*0.72), True)

F_DISPLAY_XL=display_font(56); F_DISPLAY_L=display_font(30); F_DISPLAY_M=display_font(22)

# Palette
BG=(2,7,16); PANEL=(5,13,26); GRID=(20,42,60)
WHITE=(235,245,255); CYAN=(20,190,255); GREEN=(70,220,60)
YELLOW=(255,215,30); ORANGE=(255,145,20); RED=(255,55,40)
PURPLE=(190,90,255); BLUE=(80,145,255); MUTED=(125,155,180)

# Star field
rng=np.random.default_rng(2026)
STARS=[(int(rng.integers(0,W)),int(rng.integers(0,H)),
        int(rng.integers(1,3))) for _ in range(180)]

def get_json(url):
    try:
        r=session.get(url,timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("[NOAA ERROR]",url,e,flush=True)
        return None

def records(data):
    if not isinstance(data,list) or not data: return []
    if isinstance(data[0],list):
        return [dict(zip(data[0],row)) for row in data[1:]]
    return data

def latest(data, keys):
    rows=records(data)
    for row in reversed(rows):
        for key in keys:
            try:
                v=row.get(key)
                if v not in (None,"","null"):
                    return float(v)
            except (ValueError,TypeError):
                pass
    return None

def xray_class(v):
    if v is None:return "NO DATA"
    if v>=1e-4:return f"X{v/1e-4:.1f}"
    if v>=1e-5:return f"M{v/1e-5:.1f}"
    if v>=1e-6:return f"C{v/1e-6:.1f}"
    if v>=1e-7:return f"B{v/1e-7:.1f}"
    return f"A{v/1e-8:.1f}"

def kp_status(k):
    if k is None:return "NO DATA"
    if k<2:return "QUIET"
    if k<4:return "UNSETTLED"
    if k<5:return "ACTIVE"
    if k<6:return "G1 - MINOR STORM"
    if k<7:return "G2 - MODERATE STORM"
    if k<8:return "G3 - STRONG STORM"
    if k<9:return "G4 - SEVERE STORM"
    return "G5 - EXTREME STORM"

def parse_issue_time(text):
    if not text:
        return None
    m = re.search(
        r"Issue Time:\s*(\d{4}\s+[A-Za-z]{3}\s+\d{1,2}\s+\d{4}\s+UTC)",
        str(text)
    )
    if m:
        try:
            return datetime.strptime(
                m.group(1), "%Y %b %d %H%M UTC"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def fetch_alert():
    data = get_json(URL["alerts"])
    if not isinstance(data, list):
        return "NO CURRENT ALERTS", None

    now = datetime.now(timezone.utc)
    candidates = []

    for item in data:
        if not isinstance(item, dict):
            continue

        msg = (
            item.get("message")
            or item.get("product_id")
            or item.get("product")
            or ""
        )

        issue = (
            item.get("issue_datetime")
            or item.get("issueTime")
            or item.get("issue_time")
        )

        issue_dt = None
        if issue:
            try:
                issue_dt = datetime.fromisoformat(
                    str(issue).replace("Z", "+00:00")
                )
            except ValueError:
                pass

        if issue_dt is None:
            issue_dt = parse_issue_time(msg)

        if issue_dt is not None:
            if issue_dt.tzinfo is None:
                issue_dt = issue_dt.replace(tzinfo=timezone.utc)

            age_hours = (
                now - issue_dt
            ).total_seconds() / 3600

            if -1 <= age_hours <= 24:
                candidates.append(
                    (issue_dt, str(msg))
                )

    if not candidates:
        return "NO CURRENT ALERTS", None

    issue_dt, msg = max(
        candidates,
        key=lambda item: item[0]
    )

    clean = re.sub(
        r"\\s+",
        " ",
        msg
    ).strip()

    return clean[:150], issue_dt

def particle_value(data, keys):
    rows = records(data)
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        for key in keys:
            try:
                value = row.get(key)
                if value not in (None, "", "null"):
                    return float(value)
            except (ValueError, TypeError):
                pass
    return None


def series_values(data, keys, limit=240):
    rows = records(data)
    out = []
    for row in rows:
        if not isinstance(row, dict):
            out.append(None)
            continue
        value = None
        for key in keys:
            try:
                raw = row.get(key)
                if raw not in (None, "", "null"):
                    value = float(raw)
                    break
            except (ValueError, TypeError):
                pass
        out.append(value)
    return out[-limit:]


def parse_sunspot(data):
    rows = records(data)
    vals = []
    for row in rows[-30:]:
        if not isinstance(row, dict):
            continue
        for key in ("sunspot_number", "sunspot", "ssn", "sunspot_number_daily"):
            try:
                v = row.get(key)
                if v not in (None, "", "null"):
                    vals.append(float(v)); break
            except (ValueError, TypeError):
                pass
    return vals[-1] if vals else None

def update_data():
    kp_data = get_json(URL["kp"])
    kp = latest(
        kp_data,
        ["Kp", "kp", "Kp_index", "estimated_kp"]
    )

    wind = get_json(URL["wind"])
    mag = get_json(URL["mag"])
    xr = get_json(URL["xray"])
    pr = get_json(URL["protons"])
    el = get_json(URL["electrons"])
    sunspot_data = get_json(URL["sunspots"])
    ovation_data = get_json(URL["ovation"])

    speed = latest(
        wind,
        ["speed", "proton_speed", "velocity"]
    )
    density = latest(
        wind,
        ["density", "proton_density"]
    )
    bz = latest(
        mag,
        ["bz_gsm", "bz", "Bz"]
    )
    bt = latest(
        mag,
        ["bt", "Bt", "total"]
    )
    xray = latest(
        xr,
        ["flux", "observed_flux"]
    )

    proton = particle_value(
        pr,
        ["flux", "integral_flux", "proton_flux", "flux_gt_10mev"]
    )

    electron = particle_value(
        el,
        ["flux", "integral_flux", "electron_flux", "flux_gt_2mev"]
    )
    sunspot = parse_sunspot(sunspot_data)

    alert, alert_time = fetch_alert()

    now = datetime.now(
        timezone.utc
    ).strftime("%H:%M:%S UTC")

    with lock:
        for key, value in [
            ("kp", kp),
            ("speed", speed),
            ("density", density),
            ("bz", bz),
            ("bt", bt),
            ("xray", xray),
            ("proton", proton),
            ("electron", electron),
            ("sunspot", sunspot)
        ]:
            if value is not None:
                DATA[key] = value

        if xray is not None:
            DATA["flare"] = xray_class(xray)

        DATA["alert"] = alert
        DATA["alert_time"] = (
            alert_time.strftime("%H:%M UTC")
            if alert_time else None
        )
        feed_ok = any(x is not None for x in (kp, wind, mag, xr, pr, el, sunspot_data, ovation_data))
        if feed_ok:
            DATA["updated"] = now
            DATA["last_success"] = now
        DATA["ovation_ok"] = isinstance(ovation_data, (dict, list))

        # Fill the dashboard with real recent NOAA history on every refresh.
        wind_speed = series_values(
            wind, ["speed", "proton_speed", "velocity"]
        )
        mag_bz = series_values(
            mag, ["bz_gsm", "bz", "Bz"]
        )
        kp_series = series_values(
            kp_data, ["Kp", "kp", "Kp_index", "estimated_kp"]
        )
        xray_series = series_values(
            xr, ["flux", "observed_flux"]
        )
        proton_series = series_values(
            pr, ["flux", "integral_flux", "proton_flux", "flux_gt_10mev"]
        )
        electron_series = series_values(
            el, ["flux", "integral_flux", "electron_flux", "flux_gt_2mev"]
        )

        n = max(
            len(wind_speed), len(mag_bz), len(kp_series),
            len(xray_series), len(proton_series), len(electron_series), 1
        )

        def pad(values):
            return [None] * (n - len(values)) + values

        ws, bz_s, kp_s, xr_s, pr_s, el_s = map(
            pad,
            [wind_speed, mag_bz, kp_series, xray_series, proton_series, electron_series]
        )

        DATA["history"] = [
            (i, ws[i], bz_s[i], kp_s[i], xr_s[i], pr_s[i], el_s[i])
            for i in range(n)
        ][-240:]

    print(
        "[NOAA]",
        DATA,
        flush=True
    )

def worker():
    while running:
        try:update_data()
        except Exception as e:print("[WORKER]",e,flush=True)
        for _ in range(REFRESH):
            if not running:return
            time.sleep(1)

# ============================================================
# REAL IMAGERY — NASA SDO (Sun) + NASA EPIC/DSCOVR (Earth)
# Fetched periodically in the background and circle-masked so
# they drop straight into the existing panel layout. Any
# failure falls back silently to the procedural drawings.
# ============================================================

def fetch_image_bytes(url):
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        return Image.open(BytesIO(r.content))
    except Exception as e:
        print("[IMAGE ERROR]", url, e, flush=True)
        return None

def circular_disk(im, size):
    """Center-crop to square, resize, and cut a circular alpha mask."""
    im = im.convert("RGB")
    w, h = im.size
    s = min(w, h)
    left = (w - s) // 2
    top = (h - s) // 2
    im = im.crop((left, top, left + s, top + s)).resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out

def fetch_sun_image():
    raw = fetch_image_bytes(SDO_SUN_URL)
    if raw is None:
        return None
    return circular_disk(raw, 512)

def fetch_magnetogram_image():
    raw = fetch_image_bytes(SDO_MAGNETOGRAM_URL)
    if raw is None:
        return None
    return circular_disk(raw, 360)

def fetch_continuum_image():
    raw = fetch_image_bytes(SDO_CONTINUUM_URL)
    if raw is None:
        return None
    return circular_disk(raw, 360)

def fetch_earth_image():
    try:
        r = session.get(EPIC_LIST_URL, timeout=20)
        r.raise_for_status()
        items = r.json()
        if not items:
            return None
        latest = items[-1]
        name = latest["image"]
        date = latest["date"].split(" ")[0]
        y, m, dday = date.split("-")
        img_url = f"https://epic.gsfc.nasa.gov/archive/natural/{y}/{m}/{dday}/png/{name}.png"
    except Exception as e:
        print("[EARTH IMAGE ERROR]", e, flush=True)
        return None
    raw = fetch_image_bytes(img_url)
    if raw is None:
        return None
    return circular_disk(raw, 420)

def fetch_solar_cycle_history():
    """Real monthly sunspot-number history from NOAA SWPC (SILSO-derived)."""
    data = get_json(SOLAR_CYCLE_URL)
    rows = records(data)
    if not rows:
        return None
    months, values = [], []
    for row in rows:
        if not isinstance(row, dict):
            continue
        tag = row.get("time-tag") or row.get("time_tag") or row.get("date")
        v = None
        for key in ("ssn", "ssn_smoothed", "sunspot_number", "observed_ssn"):
            raw_v = row.get(key)
            if raw_v not in (None, "", "null"):
                try:
                    v = float(raw_v)
                    break
                except (ValueError, TypeError):
                    pass
        if tag and v is not None:
            months.append(str(tag))
            values.append(v)
    if not values:
        return None
    # keep the most recent ~28 years for a solar-cycle-scale chart
    months, values = months[-336:], values[-336:]
    result = {"months": months, "values": values, "current": values[-1]}
    years = [m[:4] for m in months]
    this_year = years[-1]
    prev_year = str(int(this_year) - 1)
    this_vals = [v for y, v in zip(years, values) if y == this_year]
    prev_vals = [v for y, v in zip(years, values) if y == prev_year]
    result["this_year_avg"] = sum(this_vals) / len(this_vals) if this_vals else None
    result["prev_year_avg"] = sum(prev_vals) / len(prev_vals) if prev_vals else None
    result["prev_year_label"] = prev_year
    return result

def update_images():
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    sun_img = fetch_sun_image()
    earth_img = fetch_earth_image()
    mag_img = fetch_magnetogram_image()
    cont_img = fetch_continuum_image()
    hist = fetch_solar_cycle_history()
    with img_lock:
        if sun_img is not None:
            REAL_IMAGES["sun"] = sun_img
            REAL_IMAGES["sun_updated"] = now
        if earth_img is not None:
            REAL_IMAGES["earth"] = earth_img
            REAL_IMAGES["earth_updated"] = now
        if mag_img is not None:
            REAL_IMAGES["magnetogram"] = mag_img
        if cont_img is not None:
            REAL_IMAGES["continuum"] = cont_img
        if hist is not None:
            HISTORY.update(hist)
    print(f"[IMAGE] sun={'OK' if sun_img is not None else 'fallback'} "
          f"earth={'OK' if earth_img is not None else 'fallback'} "
          f"magnetogram={'OK' if mag_img is not None else 'fallback'} "
          f"continuum={'OK' if cont_img is not None else 'fallback'} "
          f"history={'OK' if hist is not None else 'fallback'}", flush=True)

def image_worker():
    while running:
        try:update_images()
        except Exception as e:print("[IMAGE WORKER]",e,flush=True)
        for _ in range(IMAGE_REFRESH):
            if not running:return
            time.sleep(1)

def val(v, digits=1, suffix=""):
    return "--" if v is None else f"{v:.{digits}f}{suffix}"

def panel(d,box,title,accent=CYAN):
    x1,y1,x2,y2=box
    d.rounded_rectangle(box,radius=8,fill=PANEL,outline=accent,width=1)
    d.text((x1+10,y1+8),title,font=F_H,fill=accent)

def graph(d,box,values,accent,zero=False):
    x1,y1,x2,y2=box
    # grid
    for i in range(1,5):
        y=y1+(y2-y1)*i/5
        d.line((x1,y,x2,y),fill=GRID,width=1)
    for i in range(1,6):
        x=x1+(x2-x1)*i/6
        d.line((x,y1,x,y2),fill=GRID,width=1)
    vals=[v for v in values if v is not None]
    if not vals:return
    lo=min(vals); hi=max(vals)
    if zero:
        lo=min(lo,-1); hi=max(hi,1)
    if hi-lo<1e-9: hi=lo+1
    pts=[]
    for i,v in enumerate(values):
        if v is None:continue
        xx=x1+(x2-x1)*(i/max(1,len(values)-1))
        yy=y2-(v-lo)/(hi-lo)*(y2-y1)
        pts.append((xx,yy))
    if len(pts)>1:d.line(pts,fill=accent,width=2)
    if zero and lo<0<hi:
        yy=y2-(0-lo)/(hi-lo)*(y2-y1)
        d.line((x1,yy,x2,yy),fill=(80,60,60),width=1)

def draw_sun(img,d,cx,cy,r,n):
    with img_lock:
        sun_real = REAL_IMAGES["sun"]
    # glow rings (drawn either way, sit behind/around the disk)
    for rr in range(r+25,r-2,-2):
        a=max(1,int(20*(r+25-rr)/27))
        d.ellipse((cx-rr,cy-rr,cx+rr,cy+rr),outline=(120+a*3,50+a*2,10),width=1)
    if sun_real is not None:
        diam=r*2
        disk=sun_real.resize((diam,diam),Image.LANCZOS)
        img.paste(disk,(cx-r,cy-r),disk)
        d.ellipse((cx-r,cy-r,cx+r,cy+r),outline=(255,190,30),width=2)
    else:
        d.ellipse((cx-r,cy-r,cx+r,cy+r),fill=(235,92,10),outline=(255,190,30),width=2)
        # turbulent surface
        for i in range(65):
            ang=rng.random()*math.tau
            rr=r*math.sqrt(rng.random())*.96
            x=cx+math.cos(ang)*rr; y=cy+math.sin(ang)*rr
            rad=int(rng.integers(1,5))
            d.ellipse((x-rad,y-rad,x+rad,y+rad),fill=(255,145,20))
        # active regions
        for ox,oy,rr in [(-25,-18,12),(32,-35,8),(18,27,10),(-38,30,7)]:
            d.ellipse((cx+ox-rr,cy+oy-rr,cx+ox+rr,cy+oy+rr),fill=(110,35,5))
            d.ellipse((cx+ox-rr/2,cy+oy-rr/2,cx+ox+rr/2,cy+oy+rr/2),fill=(255,205,40))
    # flare beam — always live/animated on top, real photo or not
    phase=(n%180)/180*math.tau
    ex=cx+math.cos(phase)*r*1.8; ey=cy+math.sin(phase)*r*1.8
    d.line((cx,cy,ex,ey),fill=(255,150,40),width=2)
    if sun_real is not None:
        d.text((cx-r,cy+r+8),"NASA SDO AIA 171Å • LIVE",font=F_XXS,fill=MUTED)

def aurora(img,d,box,n):
    x1,y1,x2,y2=box
    cx=(x1+x2)//2; cy=(y1+y2)//2+10
    er=min((x2-x1)//4,(y2-y1)//2)
    with img_lock:
        earth_real = REAL_IMAGES["earth"]
    if earth_real is not None:
        diam=er*2
        disk=earth_real.resize((diam,diam),Image.LANCZOS)
        img.paste(disk,(cx-er,cy-er),disk)
        d.ellipse((cx-er,cy-er,cx+er,cy+er),outline=(30,110,170),width=1)
    else:
        # stylized Earth fallback
        d.ellipse((cx-er,cy-er,cx+er,cy+er),fill=(7,20,42),outline=(30,110,170),width=1)
        # longitude/latitude
        for off in [-.6,-.3,0,.3,.6]:
            d.arc((cx-er+off*er,cy-er,cx+er+off*er,cy+er),70,290,fill=(25,70,100),width=1)
        for yy in [-.45,0,.45]:
            d.arc((cx-er,cy-er+yy*er,cx+er,cy+er+yy*er),180,360,fill=(25,70,100),width=1)
    # moving aurora oval — always live/animated on top, real photo or not
    for j in range(5):
        rr=er*(1.25+j*.04)
        alpha=100-j*12
        pts=[]
        for i in range(81):
            a=math.tau*i/80
            wob=1+0.05*math.sin(3*a+n/25+j)
            pts.append((cx+math.cos(a)*rr*wob,cy+math.sin(a)*rr*.45*wob))
        d.line(pts,fill=(40,160+min(50,alpha),80),width=2)
    if earth_real is not None:
        d.text((cx-er,cy+er+10),"NASA EPIC/DSCOVR • Earth",font=F_XXS,fill=MUTED)


# ============================================================
# FIVE DASHBOARDS — one continuous YouTube stream
# ============================================================
ROTATION_SECONDS = 2 * 60
FADE_FRAMES = 30

def header(d, title, subtitle, accent, number):
    d.rectangle((0,0,W,72), fill=(4,10,22))
    d.rounded_rectangle((15,13,105,55), radius=7, fill=(220,25,25))
    d.ellipse((28,29,38,39), fill=WHITE); d.text((44,20),"LIVE",font=F_M,fill=WHITE)
    d.text((120,15),title,font=F_TITLE,fill=WHITE)
    d.text((122,48),subtitle,font=F_XS,fill=MUTED)
    now=datetime.now(timezone.utc)
    d.text((800,10),now.strftime("%d %b %Y"),font=F_M,fill=WHITE)
    d.text((815,38),now.strftime("%H:%M:%S UTC"),font=F_M,fill=WHITE)
    d.text((1060,49),f"UPDATED {DATA['updated']}",font=F_XS,fill=GREEN)
    d.rounded_rectangle((1110,12,1265,43),radius=6,fill=(8,20,34),outline=accent,width=1)
    d.text((1122,19),f"DASHBOARD {number}/5",font=F_XS,fill=accent)

def footer(d, n, label="NOAA SWPC • LIVE DATA"):
    d.rectangle((0,660,W,H),fill=(4,10,20))
    status=kp_status(DATA["kp"])
    d.text((15,675),"SPACE WEATHER NEWS",font=F_S,fill=RED)
    d.text((205,677),f"{status} • Solar wind {val(DATA['speed'],0,' km/s')} • Bz {val(DATA['bz'],1,' nT')}",font=F_XS,fill=WHITE)
    d.text((850,677),label,font=F_XS,fill=GREEN)
    live_dot(d,1098,684,n,GREEN); d.text((1110,675),"LIVE 24/7",font=F_S,fill=GREEN)

def alert_box(d, box):
    x1,y1,x2,y2=box; alert=DATA["alert"]
    d.rounded_rectangle(box,radius=5,fill=(25,8,12),outline=RED,width=1)
    if alert=="NO CURRENT ALERTS":
        d.text((x1+18,y1+20),"NO CURRENT NOAA ALERTS",font=F_M,fill=GREEN); return
    d.text((x1+14,y1+8),"NOAA",font=F_S,fill=RED)
    words=alert.split(); lines=[]; line=""
    for word in words:
        if len(line)+len(word)+1>58: lines.append(line); line=word
        else: line=(line+" "+word).strip()
    if line: lines.append(line)
    for i,line in enumerate(lines[:2]): d.text((x1+70,y1+8+i*20),line,font=F_XS,fill=WHITE)
    if DATA["alert_time"]: d.text((x1+70,y2-18),f"Issued {DATA['alert_time']}",font=F_XS,fill=MUTED)

def scale_kp(d,x,y,w,current):
    d.text((x,y-18),"Kp 0 — 9 GEOMAGNETIC SCALE",font=F_XS,fill=MUTED)
    for i in range(10):
        xx=x+i*w/9; col=GREEN if i<4 else YELLOW if i==4 else ORANGE if i<7 else RED
        d.line((xx,y,xx,y+12),fill=col,width=5); d.text((xx-3,y+16),str(i),font=F_XXS,fill=WHITE)
    if current is not None:
        xx=x+max(0,min(9,current))*w/9; d.polygon([(xx,y-2),(xx-6,y-10),(xx+6,y-10)],fill=WHITE)

def scale_xray(d,x,y,w,flare):
    d.text((x,y-18),"GOES X-RAY CLASS",font=F_XS,fill=MUTED)
    for i,lab in enumerate(["A","B","C","M","X"]):
        xx=x+i*w/4; col=GREEN if i<2 else YELLOW if i==2 else ORANGE if i==3 else RED
        d.line((xx,y,xx,y+12),fill=col,width=5); d.text((xx-3,y+16),lab,font=F_XXS,fill=WHITE)
    if flare and flare[0] in "ABCDEMX":
        idx={"A":0,"B":1,"C":2,"M":3,"X":4}[flare[0]]; xx=x+idx*w/4
        d.polygon([(xx,y-2),(xx-6,y-10),(xx+6,y-10)],fill=WHITE)


def pulse_value(n, speed=0.08):
    return 0.5 + 0.5 * math.sin(n * speed)

def live_dot(d, x, y, n, color=GREEN):
    p = pulse_value(n, 0.12)
    r = 3 + int(3*p)
    d.ellipse((x-r,y-r,x+r,y+r), fill=color)

def animated_scan(d, x1, y1, x2, y2, n, color=CYAN, width=2):
    span=max(1,x2-x1)
    x=x1+int((n*2.4)%span)
    d.line((x,y1,x,y2),fill=color,width=width)

def animated_particles(d, x1, y1, x2, y2, n, count=12, color=CYAN):
    span=max(1,x2-x1-10)
    for i in range(count):
        phase=(n*(1.2+i*0.08)+i*47)%span
        x=x1+5+int(phase)
        y=y1+5+int((math.sin((n+i*31)*0.045)*0.5+0.5)*max(1,y2-y1-10))
        r=1+(i%3)
        d.ellipse((x-r,y-r,x+r,y+r),fill=color)

def animated_sweep(d, box, n, color=YELLOW):
    x1,y1,x2,y2=box
    span=max(1,x2-x1)
    x=x1+int((n*3.0)%span)
    d.line((x,y1,x,y2),fill=color,width=1)

def animated_wave(d, x1, y1, x2, y2, n, color=CYAN):
    pts=[]
    for i in range(81):
        t=i/80
        x=x1+(x2-x1)*t
        y=(y1+y2)/2 + math.sin(t*math.tau*3 + n*0.06)*((y2-y1)*0.22)
        pts.append((x,y))
    d.line(pts,fill=color,width=2)

def dashboard1(n):
    img=Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(img)
    for x,y,r in STARS: d.ellipse((x-r,y-r,x+r,y+r),fill=(100,100,100))
    header(d,"SPACE WEATHER LIVE","NOAA SPACE WEATHER PREDICTION CENTER",CYAN,1)
    panel(d,(12,82,300,390),"THE SUN",YELLOW); panel(d,(12,400,300,650),"SOLAR FLARE (X-RAY)",YELLOW)
    panel(d,(310,82,755,390),"SOLAR WIND",CYAN); panel(d,(765,82,1268,305),"GEOMAGNETIC ACTIVITY",GREEN)
    panel(d,(765,315,1268,510),"AURORA (OVAL)",GREEN); panel(d,(310,400,530,650),"PROTON FLUX (>10 MeV)",CYAN)
    panel(d,(540,400,755,650),"ELECTRON FLUX (>2 MeV)",PURPLE); panel(d,(765,520,1268,650),"LATEST NOAA ALERTS",RED)
    draw_sun(img,d,155,220,105,n)
    d.text((28,352),"ACTIVE SOLAR REGIONS",font=F_XS,fill=WHITE); live_dot(d,202,360,n,YELLOW); d.text((213,347),"LIVE",font=F_M,fill=YELLOW)
    cards=[("SPEED",val(DATA["speed"],0," km/s"),CYAN),("DENSITY",val(DATA["density"],1," p/cm³"),CYAN),("Bz",val(DATA["bz"],1," nT"),RED if DATA["bz"] is not None and DATA["bz"]<0 else CYAN),("Bt",val(DATA["bt"],1," nT"),CYAN)]
    for i,(lab,v,col) in enumerate(cards):
        x=322+(i%2)*210; y=120+(i//2)*80; d.rounded_rectangle((x,y,x+195,y+68),radius=6,fill=(4,11,23),outline=GRID,width=1)
        d.text((x+12,y+8),lab,font=F_XS,fill=MUTED); d.text((x+12,y+32),v,font=font(18,True),fill=col)
    hist=DATA["history"]; d.text((322,286),"SOLAR WIND SPEED — ROLLING HISTORY",font=F_XS,fill=MUTED); graph(d,(322,304,743,370),[h[1] for h in hist],CYAN); animated_scan(d,322,304,743,370,n,CYAN,1)
    kp=DATA["kp"]; d.text((785,120),"PLANETARY Kp",font=F_XS,fill=MUTED); d.text((785,145),val(kp),font=F_B,fill=YELLOW); d.text((930,145),kp_status(kp),font=F_M,fill=GREEN if (kp or 0)<5 else ORANGE)
    vals=[h[3] for h in hist[-7:] if h[3] is not None]
    for i,v in enumerate(vals):
        x=815+i*55; bh=28+int(min(v,9)*15); col=GREEN if v<4 else YELLOW if v<5 else ORANGE if v<7 else RED; d.rectangle((x,260-bh,x+35,260),fill=col); d.text((x+5,246-bh),f"{v:.1f}",font=F_XS,fill=WHITE)
    live_dot(d,785,282,n,GREEN); d.text((795,274),"Kp trend • latest NOAA values",font=F_XS,fill=MUTED)
    aurora(img,d,(780,340,1115,500),n); state="QUIET" if kp is None or kp<2 else "UNSETTLED" if kp<4 else "ACTIVE"
    d.text((1130,350),"ACTIVITY",font=F_XS,fill=MUTED); d.text((1130,380),state,font=F_M,fill=GREEN if state=="QUIET" else YELLOW); d.text((1130,430),"LIVE Kp",font=F_XS,fill=MUTED); d.text((1130,448),val(kp),font=F_M,fill=WHITE)
    d.text((28,428),"CURRENT CLASS",font=F_XS,fill=MUTED); d.text((28,453),DATA["flare"],font=F_B,fill=YELLOW); x=DATA["xray"]; d.text((155,430),"X-RAY FLUX",font=F_XS,fill=MUTED); d.text((155,455),f"{x:.2e} W/m²" if x else "--",font=F_S,fill=WHITE)
    graph(d,(28,492,285,625),[h[4] for h in hist],YELLOW); animated_scan(d,28,492,285,625,n,YELLOW,1); scale_xray(d,28,633,250,DATA["flare"])
    graph(d,(322,458,518,620),[h[5] for h in hist],CYAN); graph(d,(552,458,743,620),[h[6] for h in hist],PURPLE); animated_scan(d,322,458,518,620,n,CYAN,1); animated_scan(d,552,458,743,620,n,PURPLE,1)
    d.text((322,430),"NOAA GOES PROTONS >10 MeV",font=F_XS,fill=MUTED); d.text((552,430),"NOAA GOES ELECTRONS >2 MeV",font=F_XS,fill=MUTED)
    d.text((322,625),val(DATA["proton"],2," pfu"),font=F_XS,fill=CYAN); d.text((552,625),val(DATA["electron"],2," e/(cm²·s·sr)"),font=F_XXS,fill=PURPLE); alert_box(d,(785,550,1248,625)); footer(d,n); return img

def dashboard2(n):
    img=Image.new("RGB",(W,H),(16,16,20)); d=ImageDraw.Draw(img)
    header(d,"SOLAR ACTIVITY MONITOR","SUNSPOT • GOES X-RAY • SOLAR ACTIVITY",YELLOW,2)
    panel(d,(12,82,365,360),"SUNSPOT ACTIVITY",YELLOW); panel(d,(375,82,765,360),"SOLAR DISK",ORANGE); panel(d,(775,82,1268,360),"CURRENT SOLAR CONDITIONS",YELLOW)
    panel(d,(12,370,635,650),"GOES X-RAY HISTORY",YELLOW); panel(d,(645,370,1268,650),"FLARE / GEOMAGNETIC SUMMARY",ORANGE)
    d.text((38,130),"CURRENT SUNSPOT NUMBER",font=F_S,fill=MUTED); d.text((38,165),val(DATA.get("sunspot"),0),font=font(48,True),fill=WHITE)
    d.text((38,245),"NOAA DAILY SUNSPOT REPORT",font=F_XS,fill=YELLOW); d.text((38,275),"Live value shown when the NOAA report feed is available.",font=F_XS,fill=MUTED)
    draw_sun(img,d,570,220,125,n); animated_sweep(d,(395,95,745,345),n,ORANGE); live_dot(d,735,103,n,ORANGE); d.text((410,330),"LIVE SOLAR VISUALIZATION • NOAA MEASUREMENTS BELOW",font=F_XS,fill=MUTED)
    vals=[("X-RAY",DATA["flare"],YELLOW),("Kp",val(DATA["kp"]),YELLOW),("SOLAR WIND",val(DATA["speed"],0," km/s"),CYAN),("Bz",val(DATA["bz"],1," nT"),RED if DATA["bz"] is not None and DATA["bz"]<0 else CYAN),("DENSITY",val(DATA["density"],1," p/cm³"),CYAN),("SUNSPOTS",val(DATA.get("sunspot"),0),WHITE)]
    for i,(lab,v,col) in enumerate(vals):
        x=795+(i%2)*220; y=125+(i//2)*70; d.text((x,y),lab,font=F_XS,fill=MUTED); d.text((x,y+24),v,font=F_M,fill=col)
    graph(d,(35,425,610,615),[h[4] for h in DATA["history"]],YELLOW); animated_scan(d,35,425,610,615,n,YELLOW,1); d.text((35,620),"GOES X-RAY • 1-DAY FEED • LIVE",font=F_XS,fill=MUTED)
    d.text((675,425),"CURRENT FLARE CLASS",font=F_S,fill=MUTED); d.text((675,455),DATA["flare"],font=font(42,True),fill=YELLOW); scale_xray(d,675,520,520,DATA["flare"])
    d.text((675,570),"GEOMAGNETIC",font=F_S,fill=MUTED); d.text((675,595),kp_status(DATA["kp"]),font=F_M,fill=GREEN if (DATA["kp"] or 0)<4 else ORANGE); live_dot(d,1235,592,n,GREEN if (DATA["kp"] or 0)<4 else ORANGE); footer(d,n,"NOAA SWPC • GOES X-RAY • SUNSPOT REPORT"); return img

def dashboard3(n):
    img=Image.new("RGB",(W,H),(3,9,18)); d=ImageDraw.Draw(img)
    header(d,"SOLAR WIND MONITOR","REAL-TIME SOLAR WIND • IMF Bz / Bt",CYAN,3)
    panel(d,(12,82,1268,235),"SOLAR WIND SPEED",CYAN); panel(d,(12,250,410,470),"WIND PARAMETERS",CYAN); panel(d,(425,250,835,470),"INTERPLANETARY MAGNETIC FIELD",BLUE); panel(d,(850,250,1268,470),"STREAM INDICATOR",ORANGE)
    panel(d,(12,485,630,650),"SOLAR WIND SPEED HISTORY",CYAN); panel(d,(640,485,1268,650),"Bz MAGNETIC FIELD HISTORY",BLUE)
    d.text((40,125),"CURRENT",font=F_S,fill=MUTED); d.text((40,153),val(DATA["speed"],0," km/s"),font=font(44,True),fill=CYAN)
    speed=DATA["speed"] or 0; bar=min(100,max(0,speed/800*100)); d.rounded_rectangle((400,175,1185,202),radius=8,fill=(10,25,40),outline=GRID,width=1); d.rounded_rectangle((405,180,405+7.75*bar,197),radius=6,fill=CYAN); animated_particles(d,405,180,1180,197,n,14,CYAN)
    d.text((40,205),"Real-time solar-wind speed from NOAA RTSW feed",font=F_XS,fill=MUTED)
    for i,(lab,v,col) in enumerate([("DENSITY",val(DATA["density"],1," p/cm³"),CYAN),("Bz",val(DATA["bz"],1," nT"),RED if DATA["bz"] is not None and DATA["bz"]<0 else CYAN),("Bt",val(DATA["bt"],1," nT"),BLUE),("Kp",val(DATA["kp"]),YELLOW)]):
        x=35+(i%2)*185; y=290+(i//2)*85; d.text((x,y),lab,font=F_XS,fill=MUTED); d.text((x,y+22),v,font=F_M,fill=col)
    d.text((440,295),"Bz GSM",font=F_XS,fill=MUTED); d.text((440,320),val(DATA["bz"],1," nT"),font=F_B,fill=RED if DATA["bz"] is not None and DATA["bz"]<0 else CYAN); d.text((650,295),"Bt",font=F_XS,fill=MUTED); d.text((650,320),val(DATA["bt"],1," nT"),font=F_B,fill=BLUE)
    south="SOUTHWARD" if DATA["bz"] is not None and DATA["bz"]<0 else "NORTHWARD / WEAK"; d.text((440,385),south,font=F_M,fill=RED if south=="SOUTHWARD" else GREEN)
    d.text((875,295),"STREAM STATUS",font=F_XS,fill=MUTED); shock="ELEVATED" if speed>600 else "NORMAL"; d.text((875,320),shock,font=F_B,fill=ORANGE if shock=="ELEVATED" else GREEN); d.text((875,380),"Kp",font=F_XS,fill=MUTED); d.text((875,405),val(DATA["kp"]),font=F_M,fill=YELLOW); d.text((1050,380),"Bz",font=F_XS,fill=MUTED); d.text((1050,405),val(DATA["bz"],1," nT"),font=F_M,fill=RED if DATA["bz"] is not None and DATA["bz"]<0 else CYAN); d.text((875,440),"Indicator only — not an official CME detector.",font=F_XXS,fill=MUTED)
    graph(d,(35,535,610,630),[h[1] for h in DATA["history"]],CYAN); graph(d,(665,535,1245,630),[h[2] for h in DATA["history"]],BLUE,zero=True); animated_scan(d,35,535,610,630,n,CYAN,1); animated_scan(d,665,535,1245,630,n,BLUE,1); animated_particles(d,35,535,610,630,n,8,CYAN); animated_particles(d,665,535,1245,630,n,8,BLUE); footer(d,n,"NOAA SWPC • RTSW WIND • RTSW MAG"); return img

def dashboard4(n):
    img=Image.new("RGB",(W,H),(1,8,16)); d=ImageDraw.Draw(img)
    header(d,"AURORA & GEOMAGNETIC","PLANETARY Kp • AURORA OVAL",GREEN,4)
    panel(d,(12,82,835,650),"AURORA OVAL — LIVE GEOMAGNETIC VIEW",GREEN); panel(d,(850,82,1268,275),"CURRENT CONDITIONS",GREEN); panel(d,(850,290,1268,470),"Kp TREND & SCALE",GREEN); panel(d,(850,485,1268,650),"AURORA FEED STATUS",GREEN)
    aurora(img,d,(80,135,770,570),n); animated_particles(d,130,160,720,545,n,18,(40,220,100)); d.text((110,595),"LIVE AURORA VISUALIZATION • SYNCHRONIZED TO PLANETARY Kp",font=F_XS,fill=MUTED)
    kp=DATA["kp"]; d.text((885,125),"PLANETARY Kp",font=F_XS,fill=MUTED); d.text((885,150),val(kp),font=font(40,True),fill=YELLOW); d.text((885,210),kp_status(kp),font=F_M,fill=GREEN if (kp or 0)<4 else ORANGE); d.text((1080,125),"Bz",font=F_XS,fill=MUTED); d.text((1080,150),val(DATA["bz"],1," nT"),font=F_M,fill=RED if DATA["bz"] is not None and DATA["bz"]<0 else CYAN); d.text((1080,205),"WIND",font=F_XS,fill=MUTED); d.text((1080,230),val(DATA["speed"],0," km/s"),font=F_M,fill=CYAN)
    scale_kp(d,885,335,320,kp); graph(d,(885,390,1238,455),[h[3] for h in DATA["history"]],GREEN); animated_scan(d,885,390,1238,455,n,GREEN,1); d.text((885,468),"RECENT NOAA Kp VALUES • LIVE",font=F_XS,fill=MUTED)
    d.text((885,520),"OVATION JSON FEED",font=F_XS,fill=MUTED); d.text((885,548),"AVAILABLE" if DATA.get("ovation_ok") else "UNAVAILABLE",font=F_M,fill=GREEN if DATA.get("ovation_ok") else ORANGE); d.text((885,590),"No invented probability is shown.",font=F_XS,fill=WHITE); d.text((885,612),"Use official OVATION products for probability maps.",font=F_XXS,fill=MUTED); footer(d,n,"NOAA SWPC • PLANETARY Kp • OVATION"); return img

def dashboard5(n):
    img=Image.new("RGB",(W,H),(7,8,12)); d=ImageDraw.Draw(img)
    header(d,"SPACE WEATHER CENTER","NOAA ALERTS • LIVE CONDITIONS • DATA SOURCES",RED,5)
    panel(d,(12,82,1268,245),"CURRENT NOAA ALERT / WARNING",RED); panel(d,(12,260,410,650),"LIVE CONDITIONS",CYAN); panel(d,(425,260,835,650),"SPACE WEATHER INDICATORS",YELLOW); panel(d,(850,260,1268,650),"DATA SOURCES & STREAM STATUS",GREEN)
    alert_box(d,(40,125,1240,225)); live_dot(d,1225,140,n,RED if DATA["alert"]!="NO CURRENT ALERTS" else GREEN)
    conditions=[("X-RAY",DATA["flare"],YELLOW),("Kp",val(DATA["kp"]),YELLOW),("WIND",val(DATA["speed"],0," km/s"),CYAN),("DENSITY",val(DATA["density"],1," p/cm³"),CYAN),("Bz",val(DATA["bz"],1," nT"),RED if DATA["bz"] is not None and DATA["bz"]<0 else CYAN),("SUNSPOTS",val(DATA.get("sunspot"),0),WHITE)]
    for i,(lab,v,col) in enumerate(conditions):
        x=35+(i%2)*185; y=300+(i//2)*100; d.rounded_rectangle((x,y,x+165,y+76),radius=6,fill=(4,11,23),outline=GRID,width=1); d.text((x+10,y+10),lab,font=F_XS,fill=MUTED); d.text((x+10,y+34),v,font=F_M,fill=col)
    d.text((450,305),"SOLAR ACTIVITY",font=F_S,fill=MUTED); d.text((450,332),DATA["flare"],font=F_B,fill=YELLOW); scale_xray(d,450,390,340,DATA["flare"]); d.text((450,440),"GEOMAGNETIC",font=F_S,fill=MUTED); d.text((450,468),kp_status(DATA["kp"]),font=F_M,fill=GREEN if (DATA["kp"] or 0)<4 else ORANGE); scale_kp(d,450,520,340,DATA["kp"]); d.text((450,585),"PARTICLE FLUX",font=F_S,fill=MUTED); d.text((450,610),f"Protons {val(DATA['proton'],2)}  Electrons {val(DATA['electron'],2)}",font=F_XS,fill=WHITE)
    sources=["NOAA SWPC planetary Kp","NOAA RTSW solar wind","NOAA RTSW magnetic field","GOES X-ray / particle feeds","NOAA alerts.json","NOAA sunspot report","NOAA OVATION feed"]
    for i,s in enumerate(sources):
        c=GREEN if ((n//10+i)%7)!=0 else CYAN
        d.ellipse((875,315+i*35,883,323+i*35),fill=c); d.text((892,311+i*35),s,font=F_XS,fill=WHITE)
    animated_sweep(d,(870,305,1245,555),n,GREEN)
    d.text((875,570),"STREAM",font=F_XS,fill=MUTED); d.text((875,592),"● LIVE 24/7",font=F_M,fill=GREEN); d.text((875,625),f"Updated {DATA['updated']}",font=F_XS,fill=MUTED); footer(d,n,"NOAA SWPC • LIVE DATA • CONTINUOUS STREAM"); return img

GOLD=(230,175,60); STEEL_LT=(225,228,232); STEEL_DK=(120,124,130); PAPER=(238,236,228)

def metallic_header_bg(w,h,n):
    """Brushed-steel horizontal gradient for the documentary-style title bar."""
    x=np.linspace(0,1,w,dtype=np.float32)
    base=0.55+0.30*np.sin(x*math.pi)+0.03*np.sin(x*40+n*0.02)
    base=np.clip(base,0,1)
    row=(base*255).astype(np.uint8)
    arr=np.tile(row,(h,1))
    rgb=np.stack([ (arr*0.92).astype(np.uint8), (arr*0.93).astype(np.uint8), (arr*0.97).astype(np.uint8) ],axis=-1)
    return Image.fromarray(rgb,"RGB")

def corner_bracket_panel(d,box,accent=GOLD,ln=16,w=3):
    x1,y1,x2,y2=box
    d.rectangle(box,fill=(10,10,10))
    for cx,cy,dx,dy in [(x1,y1,1,1),(x2,y1,-1,1),(x1,y2,1,-1),(x2,y2,-1,-1)]:
        d.line((cx,cy,cx+dx*ln,cy),fill=accent,width=w)
        d.line((cx,cy,cx,cy+dy*ln),fill=accent,width=w)

def paste_vertical_label(img,x,y,text,fnt,fill=WHITE):
    tmp=Image.new("RGBA",(400,40),(0,0,0,0))
    td=ImageDraw.Draw(tmp)
    td.text((0,0),text,font=fnt,fill=fill)
    bbox=td.textbbox((0,0),text,font=fnt)
    tw,th=bbox[2]-bbox[0],bbox[3]-bbox[1]
    tmp=tmp.crop((0,0,tw+4,th+4))
    rot=tmp.rotate(90,expand=True)
    img.paste(rot,(x,y),rot)

def trend_triangle(d,x,y,up,size=7,color=GREEN):
    if up:
        d.polygon([(x,y-size),(x-size,y+size*0.7),(x+size,y+size*0.7)],fill=color)
    else:
        d.polygon([(x,y+size),(x-size,y-size*0.7),(x+size,y-size*0.7)],fill=color)

def draw_synoptic_map(d,box,n):
    """Stylized, illustrative solar synoptic-style polar chart (not a literal NOAA product)."""
    x1,y1,x2,y2=box
    cx=(x1+x2)//2; cy=(y1+y2)//2
    r=min((x2-x1),(y2-y1))//2-10
    d.ellipse((cx-r,cy-r,cx+r,cy+r),fill=PAPER,outline=(60,60,60),width=2)
    for lab,ang in [("N",-90),("S",90),("E",180),("W",0)]:
        lx=cx+math.cos(math.radians(ang))*(r+16); ly=cy+math.sin(math.radians(ang))*(r+16)
        d.text((lx-5,ly-7),lab,font=F_S,fill=(50,50,50))
    for gr in (0.33,0.66,1.0):
        d.arc((cx-r*gr,cy-r*gr,cx+r*gr,cy+r*gr),0,360,fill=(190,190,180),width=1)
    for a in range(0,360,30):
        d.line((cx,cy,cx+math.cos(math.radians(a))*r,cy+math.sin(math.radians(a))*r),fill=(205,205,196),width=1)
    seed_rng=np.random.default_rng(int(datetime.now(timezone.utc).strftime("%Y%m%d")))
    for c in range(6):
        ccx=cx+seed_rng.uniform(-0.55,0.55)*r; ccy=cy+seed_rng.uniform(-0.55,0.55)*r
        rr=seed_rng.uniform(0.08,0.2)*r
        pts=[]
        for i in range(41):
            a=math.tau*i/40
            wob=1+0.35*math.sin(a*seed_rng.uniform(2,4)+c)
            pts.append((ccx+math.cos(a)*rr*wob,ccy+math.sin(a)*rr*wob*0.7))
        d.line(pts,fill=(70,70,70),width=1)
    d.text((x1+10,y1+6),f"Date {datetime.now(timezone.utc).strftime('%Y %b %d')}  Time {datetime.now(timezone.utc).strftime('%H%M')} UT",font=F_XXS,fill=(70,70,70))
    d.text((x1+10,y2-16),"SWPC-style Solar Synoptic Chart (illustrative)",font=F_XXS,fill=(90,90,90))

def draw_channel_logo(d,cx,cy,r,n):
    p=pulse_value(n,0.05)
    d.ellipse((cx-r,cy-r,cx+r,cy+r),fill=(10,10,14),outline=GOLD,width=2)
    pr=int(r*0.62)
    d.ellipse((cx-pr,cy-pr,cx+pr,cy+pr),fill=(20,30,55),outline=(90,120,170),width=1)
    d.ellipse((cx-r+3,cy-int(r*0.28),cx+r-3,cy+int(r*0.28)),outline=GOLD,width=2)
    for i in range(5):
        a=(n*0.6+i*72)%360
        sx=cx+math.cos(math.radians(a))*r*1.35; sy=cy+math.sin(math.radians(a))*r*0.5
        rr=1+int(1.5*p)
        d.ellipse((sx-rr,sy-rr,sx+rr,sy+rr),fill=WHITE)

TICKER_MSGS=[]
def build_ticker_text():
    speed=val(DATA["speed"],0," KM/S"); kp=val(DATA["kp"]); flare=DATA["flare"]
    parts=[
        f"WEATHER NOTICE: ESTIMATED SOLAR WIND VELOCITY {speed}",
        f"PLANETARY Kp INDEX {kp} — {kp_status(DATA['kp'])}",
        f"CURRENT X-RAY CLASS {flare}",
        DATA["alert"] if DATA["alert"]!="NO CURRENT ALERTS" else "NO CURRENT NOAA ALERTS",
    ]
    return "     •     ".join(parts) + "     •     "

def documentary_ticker(img,d,n):
    bar=(0,660,W,H)
    d.rectangle(bar,fill=(8,8,10))
    d.line((0,660,W,660),fill=GOLD,width=2)
    draw_channel_logo(d,55,690,30,n)
    d.text((95,675),CHANNEL_NAME,font=font(13,True),fill=WHITE)
    d.text((95,696),"LIVE 24/7",font=F_XXS,fill=GOLD)
    text=build_ticker_text()
    tmp=Image.new("RGBA",(6000,30),(0,0,0,0))
    td=ImageDraw.Draw(tmp)
    td.text((0,4),text*3,font=font(19,True),fill=(230,225,210))
    bbox=td.textbbox((0,4),text,font=font(19,True))
    seg_w=max(1,bbox[2]-bbox[0]+90)
    offset=int((n*3.2)%seg_w)
    ticker_x1,ticker_y1,ticker_x2,ticker_y2=300,668,1030,700
    crop=tmp.crop((offset,0,offset+(ticker_x2-ticker_x1),30))
    img.paste(crop,(ticker_x1,ticker_y1),crop)
    now=datetime.now(timezone.utc)
    d.text((1045,668),now.strftime("%b %d, %Y"),font=font(15,True),fill=WHITE)
    d.text((1045,690),now.strftime("%H:%M:%S"),font=display_font(26),fill=GOLD)
    d.text((1225,668),"U",font=F_XS,fill=MUTED); d.text((1225,682),"T",font=F_XS,fill=MUTED); d.text((1225,696),"C",font=F_XS,fill=MUTED)

def dashboard6(n):
    img=Image.new("RGB",(W,H),(6,6,8)); d=ImageDraw.Draw(img)
    grad=metallic_header_bg(W,72,n); img.paste(grad,(0,0))
    d=ImageDraw.Draw(img)
    title1="SPACE"; title2="WEATHER MONITOR"
    w1=d.textlength(title1,font=F_DISPLAY_XL)
    total_w=w1+d.textlength(" "+title2,font=F_DISPLAY_XL)
    sx=(W-total_w)//2
    for off in range(6,0,-2):
        d.text((sx-off,18-off//3),title1,font=F_DISPLAY_XL,fill=(255,190,80))
    d.text((sx,18),title1,font=F_DISPLAY_XL,fill=(20,16,10))
    d.text((sx+w1+14,18),title2,font=F_DISPLAY_XL,fill=(20,16,10))

    rowA=(12,82,660,358); rowB=(672,82,1268,358)
    rowC=(12,368,660,650); rowD=(672,368,1268,650)
    corner_bracket_panel(d,rowA); corner_bracket_panel(d,rowB)
    corner_bracket_panel(d,rowC); corner_bracket_panel(d,rowD)

    # Panel A — Sunspot Activity
    x1,y1,x2,y2=rowA
    d.text((x1+18,y1+16),"SUNSPOT ACTIVITY",font=font(22,True),fill=WHITE)
    cur=DATA.get("sunspot"); prev=HISTORY.get("this_year_avg")
    d.text((x1+18,y1+70),"CURRENT AMOUNT",font=F_S,fill=STEEL_DK)
    d.text((x1+18,y1+96),val(cur,0),font=display_font(46),fill=WHITE)
    if cur is not None and prev is not None:
        trend_triangle(d,x1+150,y1+112,cur>=prev,color=GREEN if cur>=prev else RED)
    d.text((x1+18,y1+165),"LAST YEAR'S AVERAGE",font=F_S,fill=STEEL_DK)
    lya=HISTORY.get("prev_year_avg")
    d.text((x1+18,y1+191),val(lya,1),font=display_font(38),fill=WHITE)
    if lya is not None and prev is not None:
        trend_triangle(d,x1+150,y1+206,prev>=lya,color=GREEN if prev>=lya else RED)
        pct=(prev-lya)/lya*100 if lya else 0
        d.text((x1+18,y1+245),f"{pct:+.1f}% vs {HISTORY.get('prev_year_label','—')} ({lya:.1f})",font=F_S,fill=GREEN if pct>=0 else RED)
    else:
        d.text((x1+18,y1+245),"Awaiting NOAA solar-cycle history feed...",font=F_XS,fill=STEEL_DK)
    with img_lock:
        mag_thumb=REAL_IMAGES.get("magnetogram")
    tcx,tcy,tr=x2-165,y1+(y2-y1)//2,110
    d.ellipse((tcx-tr,tcy-tr,tcx+tr,tcy+tr),fill=(15,15,15),outline=(70,70,70),width=1)
    if mag_thumb is not None:
        disk=mag_thumb.resize((tr*2,tr*2),Image.LANCZOS)
        img.paste(disk,(tcx-tr,tcy-tr),disk)
        d=ImageDraw.Draw(img)
    d.text((tcx-tr,tcy+tr+8),"SDO/HMI MAGNETOGRAM",font=F_XXS,fill=STEEL_DK)

    # Panel B — Solar Dynamics Observatory
    x1,y1,x2,y2=rowB
    paste_vertical_label(img,x1+6,y1+30,"Solar Dynamics Observatory",font(20,True),WHITE)
    d=ImageDraw.Draw(img)
    with img_lock:
        mag=REAL_IMAGES.get("magnetogram"); cont=REAL_IMAGES.get("continuum")
    r2=110
    c1x,c1y=x1+175,(y1+y2)//2
    c2x,c2y=x2-160,(y1+y2)//2
    for (cxp,cyp,im,lab) in [(c1x,c1y,mag,"MAGNETOGRAM"),(c2x,c2y,cont,"CONTINUUM (VISIBLE LIGHT)")]:
        d.ellipse((cxp-r2,cyp-r2,cxp+r2,cyp+r2),fill=(15,15,15),outline=(70,70,70),width=1)
        if im is not None:
            disk=im.resize((r2*2,r2*2),Image.LANCZOS)
            img.paste(disk,(cxp-r2,cyp-r2),disk)
            d=ImageDraw.Draw(img)
        d.text((cxp-r2,cyp+r2+8),lab,font=F_XXS,fill=STEEL_DK)
    d.text((x1+18,y1+16),"NASA SDO • LIVE FULL-DISK IMAGERY",font=F_XS,fill=GOLD)

    # Panel C — Sunspot Historical
    x1,y1,x2,y2=rowC
    paste_vertical_label(img,x1+6,y1+30,"Sunspot Historical",font(20,True),WHITE)
    d=ImageDraw.Draw(img)
    gx1,gy1,gx2,gy2=x1+60,y1+35,x2-25,y2-25
    vals=HISTORY.get("values") or []
    months=HISTORY.get("months") or []
    if vals:
        hi=max(50,max(vals))
        for gv in range(0,int(hi)+1,50 if hi>200 else 20):
            yy=gy2-(gv/hi)*(gy2-gy1)
            d.line((gx1,yy,gx2,yy),fill=(35,35,35),width=1)
            d.text((gx1-32,yy-6),str(gv),font=F_XXS,fill=STEEL_DK)
        pts=[(gx1,gy2)]
        for i,v in enumerate(vals):
            xx=gx1+(gx2-gx1)*(i/max(1,len(vals)-1))
            yy=gy2-(v/hi)*(gy2-gy1)
            pts.append((xx,yy))
        pts.append((gx2,gy2))
        d.polygon(pts,fill=(120,90,10))
        d.line(pts[1:-1],fill=GOLD,width=2)
        year_ticks=sorted(set(m[:4] for m in months))
        step=max(1,len(year_ticks)//6)
        for yr in year_ticks[::step]:
            idx=next((i for i,m in enumerate(months) if m.startswith(yr)),None)
            if idx is None: continue
            xx=gx1+(gx2-gx1)*(idx/max(1,len(vals)-1))
            d.text((xx-14,gy2+8),yr,font=F_XS,fill=STEEL_DK)
    else:
        d.text((gx1,gy1+80),"Awaiting NOAA solar-cycle history feed...",font=F_S,fill=STEEL_DK)
    d.text((x1+18,y1+16),"MONTHLY SUNSPOT NUMBER • NOAA SWPC",font=F_XS,fill=GOLD)

    # Panel D — Synoptic Map
    x1,y1,x2,y2=rowD
    paste_vertical_label(img,x1+6,y1+30,"Synoptic Map",font(20,True),WHITE)
    d=ImageDraw.Draw(img)
    draw_synoptic_map(d,(x1+55,y1+30,x2-20,y2-20),n)

    documentary_ticker(img,d,n)
    d=ImageDraw.Draw(img)
    return img

DASHBOARDS=[dashboard1,dashboard2,dashboard3,dashboard4,dashboard5,dashboard6]

def current_dashboard(ts=None):
    if ts is None: ts=time.time()
    return int(ts//ROTATION_SECONDS)%len(DASHBOARDS)

def next_dashboard_seconds(ts=None):
    if ts is None: ts=time.time()
    return ROTATION_SECONDS-(ts%ROTATION_SECONDS)

def overlay(d,idx,remaining):
    d.rounded_rectangle((1035,628,1265,655),radius=5,fill=(4,10,20),outline=GRID,width=1)
    d.text((1045,633),f"PANEL {idx+1}/{len(DASHBOARDS)} • NEXT {int(remaining)//60:02d}:{int(remaining)%60:02d}",font=F_XXS,fill=WHITE)

def parse_music_urls():
    """Parse multiple direct HTTP(S) audio URLs."""
    raw = os.getenv("MUSIC_URLS", "").strip()
    if not raw:
        return []
    parts = re.split(r"[\s,;]+", raw)
    seen = set()
    urls = []
    for item in parts:
        u = item.strip()
        if not u or not re.match(r"^https?://", u, re.I):
            continue
        if u not in seen:
            seen.add(u)
            urls.append(u)
    print(f"[MUSIC] MUSIC_URLS contains {len(urls)} URL(s)", flush=True)
    return urls


def prepare_music_playlist():
    """Download, normalize, and merge all tracks into one seamless loop file."""
    urls = parse_music_urls()
    if not urls:
        print("[MUSIC] No MUSIC_URLS configured -> SILENT AUDIO", flush=True)
        return None

    os.makedirs(MUSIC_DIR, exist_ok=True)
    playlist_lines = []

    for i, url in enumerate(urls, 1):
        raw_path = os.path.join(MUSIC_DIR, f"source_{i:02d}")
        normalized = os.path.join(MUSIC_DIR, f"track_{i:02d}.m4a")
        try:
            print(f"[MUSIC] Downloading {i}/{len(urls)}: {url}", flush=True)
            tmp = raw_path + ".part"
            with session.get(url, stream=True, timeout=(15, 120), allow_redirects=True) as r:
                r.raise_for_status()
                total = 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
                            total += len(chunk)
            if total < 4096:
                raise RuntimeError(f"download too small ({total} bytes)")
            os.replace(tmp, raw_path)

            print(f"[MUSIC] Converting track {i} -> AAC 44.1 kHz stereo", flush=True)
            tmp_m4a = normalized + ".part.m4a"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", raw_path,
                "-vn", "-c:a", "aac", "-b:a", "128k",
                "-ar", "44100", "-ac", "2",
                tmp_m4a
            ], check=True)
            if os.path.getsize(tmp_m4a) < 4096:
                raise RuntimeError("FFmpeg produced an empty/invalid audio file")
            os.replace(tmp_m4a, normalized)
            playlist_lines.append("file '" + normalized.replace("'", "'\\''") + "'")
            print(f"[MUSIC] Track {i} READY", flush=True)
        except Exception as e:
            print(f"[MUSIC ERROR] Track {i} failed: {e}", flush=True)
            try:
                if os.path.exists(raw_path + ".part"):
                    os.remove(raw_path + ".part")
            except OSError:
                pass

    if not playlist_lines:
        print("[MUSIC] No usable tracks -> SILENT AUDIO", flush=True)
        return None

    with open(MUSIC_PLAYLIST, "w", encoding="utf-8") as f:
        f.write("\n".join(playlist_lines) + "\n")

    # IMPORTANT: loop the single merged file, not the concat demuxer itself.
    # This avoids concat/stream_loop edge cases that can terminate audio early.
    tmp_loop = MUSIC_LOOP_FILE + ".part.m4a"
    print(f"[MUSIC] Merging {len(playlist_lines)} track(s) into one loop file", flush=True)
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", MUSIC_PLAYLIST,
        "-vn", "-c:a", "aac", "-b:a", "128k",
        "-ar", "44100", "-ac", "2", tmp_loop
    ], check=True)
    if os.path.getsize(tmp_loop) < 4096:
        raise RuntimeError("Merged music loop file is empty/invalid")
    os.replace(tmp_loop, MUSIC_LOOP_FILE)

    print(f"[MUSIC] PLAYLIST READY: {len(playlist_lines)} track(s)", flush=True)
    print("[MUSIC] Background music will loop continuously across all dashboards", flush=True)
    return MUSIC_LOOP_FILE


def main():
    if not YOUTUBE_KEY:
        print("ERROR: YOUTUBE_KEY GitHub secret is missing.",flush=True); sys.exit(1)
    threading.Thread(target=worker,daemon=True).start()
    threading.Thread(target=image_worker,daemon=True).start()
    time.sleep(3)
    stream_url=YOUTUBE_RTMP.rstrip("/")+"/"+YOUTUBE_KEY
    music_playlist = prepare_music_playlist()

    cmd=[
        "ffmpeg", "-hide_banner", "-loglevel", "info",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
        "-r", str(FPS), "-i", "-",
    ]

    if music_playlist:
        # Loop the entire playlist forever. FFmpeg decodes/encodes the music
        # to AAC for YouTube, while Python continues feeding live video frames.
        cmd += [
            "-stream_loop", "-1",
            "-i", music_playlist,
        ]
    else:
        cmd += [
            "-f", "lavfi", "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
        ]

    cmd += [
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-pix_fmt", "yuv420p", "-b:v", "3500k", "-maxrate", "4000k",
        "-bufsize", "7000k", "-g", "60", "-keyint_min", "60",
        "-sc_threshold", "0", "-c:a", "aac", "-b:a", "128k",
        "-ar", "44100", "-ac", "2", "-f", "flv", stream_url,
    ]
    interval=1.0/FPS
    while running:
        print("Starting FFmpeg -> YouTube",flush=True)
        p=subprocess.Popen(cmd,stdin=subprocess.PIPE); n=0; prev_idx=None; prev_img=None; next_frame=time.monotonic()
        try:
            while running and p.poll() is None:
                ts=time.time(); idx=current_dashboard(ts); remaining=next_dashboard_seconds(ts)
                img=DASHBOARDS[idx](n); overlay(ImageDraw.Draw(img),idx,remaining)
                if prev_idx is not None and idx!=prev_idx and prev_img is not None:
                    for step in range(FADE_FRAMES):
                        if not running or p.poll() is not None: break
                        blend=Image.blend(prev_img,img,(step+1)/FADE_FRAMES); overlay(ImageDraw.Draw(blend),idx,remaining); p.stdin.write(np.asarray(blend,dtype=np.uint8).tobytes()); n+=1; next_frame+=interval; sleep=max(0,next_frame-time.monotonic());
                        if sleep: time.sleep(sleep)
                p.stdin.write(np.asarray(img,dtype=np.uint8).tobytes()); prev_img=img.copy(); prev_idx=idx; n+=1; next_frame+=interval; sleep=max(0,next_frame-time.monotonic())
                if sleep: time.sleep(sleep)
        except (BrokenPipeError,OSError) as e:
            print("FFmpeg stopped:",e,flush=True)
        finally:
            try:p.stdin.close()
            except Exception:pass
            try:p.wait(timeout=8)
            except Exception:p.kill()
        if running:
            print("Stream exited. Restarting in 10 seconds...",flush=True); time.sleep(10)

if __name__=="__main__": main()
