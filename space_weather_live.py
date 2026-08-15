import os
import sys
import time
import math
import signal
import threading
import subprocess
import re
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

NOAA = "https://services.swpc.noaa.gov"

URL = {
    "kp": f"{NOAA}/json/planetary_k_index_1m.json",
    "wind": f"{NOAA}/json/rtsw/rtsw_wind_1m.json",
    "mag": f"{NOAA}/json/rtsw/rtsw_mag_1m.json",
    "xray": f"{NOAA}/json/goes/primary/xrays-1-day.json",
    "alerts": f"{NOAA}/products/alerts.json",
    "protons": f"{NOAA}/json/goes/primary/integral-protons-1-day.json",
    "electrons": f"{NOAA}/json/goes/primary/integral-electrons-1-day.json",
}

running = True
lock = threading.Lock()
DATA = {
    "kp": None, "speed": None, "density": None, "bz": None, "bt": None,
    "xray": None, "proton": None, "electron": None,
    "flare": "NO DATA", "alert": "NO CURRENT ALERTS",
    "updated": "Waiting for NOAA...", "history": [],
    "alert_time": None
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

# Palette
BG=(2,7,16); PANEL=(5,13,26); GRID=(20,42,60)
WHITE=(235,245,255); CYAN=(20,190,255); GREEN=(70,220,60)
YELLOW=(255,215,30); ORANGE=(255,145,20); RED=(255,55,40)
PURPLE=(190,90,255); MUTED=(125,155,180)

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
        r"Issue Time:\s*(\\d{4}\s+[A-Za-z]{3}\s+\\d{1,2}\s+\\d{4}\s+UTC)",
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


def update_data():
    kp = latest(
        get_json(URL["kp"]),
        ["Kp", "kp", "Kp_index", "estimated_kp"]
    )

    wind = get_json(URL["wind"])
    mag = get_json(URL["mag"])
    xr = get_json(URL["xray"])
    pr = get_json(URL["protons"])
    el = get_json(URL["electrons"])

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
            ("electron", electron)
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
        DATA["updated"] = now

        # Fill the dashboard with real recent NOAA history on every refresh.
        wind_speed = series_values(
            wind, ["speed", "proton_speed", "velocity"]
        )
        mag_bz = series_values(
            mag, ["bz_gsm", "bz", "Bz"]
        )
        kp_series = series_values(
            get_json(URL["kp"]), ["Kp", "kp", "Kp_index", "estimated_kp"]
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

def draw_sun(d,cx,cy,r,n):
    # glow rings
    for rr in range(r+25,r-2,-2):
        a=max(1,int(20*(r+25-rr)/27))
        d.ellipse((cx-rr,cy-rr,cx+rr,cy+rr),outline=(120+a*3,50+a*2,10),width=1)
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
    # flare beam
    phase=(n%180)/180*math.tau
    ex=cx+math.cos(phase)*r*1.8; ey=cy+math.sin(phase)*r*1.8
    d.line((cx,cy,ex,ey),fill=(255,150,40),width=2)

def aurora(d,box,n):
    x1,y1,x2,y2=box
    cx=(x1+x2)//2; cy=(y1+y2)//2+10
    # stylized Earth
    er=min((x2-x1)//4,(y2-y1)//2)
    d.ellipse((cx-er,cy-er,cx+er,cy+er),fill=(7,20,42),outline=(30,110,170),width=1)
    # longitude/latitude
    for off in [-.6,-.3,0,.3,.6]:
        d.arc((cx-er+off*er,cy-er,cx+er+off*er,cy+er),70,290,fill=(25,70,100),width=1)
    for yy in [-.45,0,.45]:
        d.arc((cx-er,cy-er+yy*er,cx+er,cy+er+yy*er),180,360,fill=(25,70,100),width=1)
    # moving aurora oval
    for j in range(5):
        rr=er*(1.25+j*.04)
        alpha=100-j*12
        pts=[]
        for i in range(81):
            a=math.tau*i/80
            wob=1+0.05*math.sin(3*a+n/25+j)
            pts.append((cx+math.cos(a)*rr*wob,cy+math.sin(a)*rr*.45*wob))
        d.line(pts,fill=(40,160+min(50,alpha),80),width=2)

def draw(n):
    img=Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(img)
    for x,y,r in STARS:
        b=int(80+70*(.5+.5*math.sin(n/25+x*.03)))
        d.ellipse((x-r,y-r,x+r,y+r),fill=(b,b,b))

    # Header
    d.rectangle((0,0,W,70),fill=(4,10,22))
    d.rounded_rectangle((15,13,105,55),radius=7,fill=(220,25,25))
    d.ellipse((28,29,38,39),fill=WHITE)
    d.text((44,20),"LIVE",font=F_M,fill=WHITE)
    d.text((120,16),"SPACE WEATHER ",font=F_TITLE,fill=WHITE)
    d.text((415,16),"LIVE",font=F_TITLE,fill=RED)
    d.text((122,48),"NOAA SPACE WEATHER PREDICTION CENTER",font=F_XS,fill=MUTED)
    d.text((770,10),datetime.now(timezone.utc).strftime("%d %b %Y"),font=F_M,fill=WHITE)
    d.text((785,38),datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),font=F_M,fill=WHITE)
    d.text((1010,49),"DATA UPDATED",font=F_XS,fill=GREEN)
    d.text((1100,49),DATA["updated"],font=F_XS,fill=GREEN)

    # Layout panels
    panel(d,(12,82,300,365),"THE SUN",YELLOW)
    panel(d,(12,375,300,515),"SOLAR FLARE (X-RAY)",YELLOW)
    panel(d,(12,525,300,650),"GOES X-RAY FLUX",YELLOW)
    panel(d,(310,82,755,365),"SOLAR WIND",CYAN)
    panel(d,(765,82,1268,300),"GEOMAGNETIC ACTIVITY",GREEN)
    panel(d,(765,310,1268,515),"AURORA (OVAL)",GREEN)
    panel(d,(310,375,530,650),"PROTON FLUX (>10 MeV)",CYAN)
    panel(d,(540,375,755,650),"ELECTRON FLUX (>2 MeV)",PURPLE)
    panel(d,(765,525,1268,650),"LATEST NOAA ALERTS",RED)

    # Sun
    draw_sun(d,155,220,92,n)
    d.text((25,338),"ACTIVE REGIONS",font=F_XS,fill=WHITE)
    d.text((185,334),"LIVE",font=F_M,fill=YELLOW)

    # Solar wind metrics — no overlap with the history graph
    hist = DATA["history"]
    cards = [
        ("SPEED", val(DATA["speed"], 0, " km/s"), CYAN),
        ("DENSITY", val(DATA["density"], 1, " p/cm³"), CYAN),
        ("Bz", val(DATA["bz"], 1, " nT"),
         RED if DATA["bz"] is not None and DATA["bz"] < 0 else CYAN),
        ("Bt", val(DATA["bt"], 1, " nT"), CYAN)
    ]

    for i, (lab, value, color) in enumerate(cards):
        x = 322 + (i % 2) * 210
        y = 122 + (i // 2) * 78

        d.rounded_rectangle(
            (x, y, x + 195, y + 68),
            radius=6,
            fill=(4, 11, 23),
            outline=GRID,
            width=1
        )

        d.text(
            (x + 12, y + 8),
            lab,
            font=F_XS,
            fill=MUTED
        )

        d.text(
            (x + 12, y + 32),
            value,
            font=font(18, True),
            fill=color
        )

    d.text(
        (322, 274),
        "SOLAR WIND SPEED — ROLLING HISTORY",
        font=F_XS,
        fill=MUTED
    )

    graph(
        d,
        (322, 292, 743, 345),
        [item[1] for item in hist],
        CYAN
    )

    # Kp
    kp=DATA["kp"]
    d.text((785,120),"PLANETARY Kp",font=F_XS,fill=MUTED)
    d.text((785,145),val(kp),font=F_B,fill=YELLOW)
    status=kp_status(kp)
    d.text((930,145),status,font=F_M,fill=ORANGE if kp and kp>=5 else GREEN)
    vals=[h[3] for h in hist[-7:] if h[3] is not None]
    for i,v in enumerate(vals):
        x=815+i*55
        bh=28+int(min(v,9)*15)
        col=GREEN if v<4 else YELLOW if v<5 else ORANGE if v<7 else RED
        d.rectangle((x,260-bh,x+35,260),fill=col)
        d.text((x+5,246-bh),f"{v:.1f}",font=F_XS,fill=WHITE)
    d.text((785,272),"Kp trend • latest NOAA values",font=F_XS,fill=MUTED)

    # Aurora — stylized oval synchronized to live geomagnetic activity.
    # The percentage shown in the earlier version was Kp-derived, not an
    # actual OVATION probability, so it is intentionally removed.
    aurora(d,(780,340,1115,500),n)
    aurora_state = "QUIET" if kp is None or kp < 2 else (
        "UNSETTLED" if kp < 4 else "ACTIVE"
    )
    d.text((1130,350),"ACTIVITY",font=F_XS,fill=MUTED)
    d.text((1130,380),aurora_state,font=F_M,fill=GREEN if aurora_state == "QUIET" else YELLOW)
    d.text((1130,430),"LIVE Kp",font=F_XS,fill=MUTED)
    d.text((1130,448),val(kp),font=F_M,fill=WHITE)

    # Flare
    d.text((28,405),"CURRENT CLASS",font=F_XS,fill=MUTED)
    d.text((28,430),DATA["flare"],font=F_B,fill=YELLOW)
    x=DATA["xray"]
    d.text((155,407),"X-RAY FLUX",font=F_XS,fill=MUTED)
    d.text((155,432),f"{x:.2e} W/m²" if x else "--",font=F_S,fill=WHITE)

    # Xray graph
    graph(d,(28,552,285,625),[h[4] for h in hist],YELLOW)
    d.text((28,632),"GOES • 1-DAY FEED",font=F_XS,fill=MUTED)

    # Real NOAA GOES integral particle feeds
    pvals = [item[5] for item in hist]
    evals = [item[6] for item in hist]

    graph(
        d,
        (322, 440, 518, 615),
        pvals,
        CYAN
    )

    graph(
        d,
        (552, 440, 743, 615),
        evals,
        PURPLE
    )

    d.text(
        (322, 420),
        "NOAA GOES PROTONS >10 MeV",
        font=F_XS,
        fill=MUTED
    )

    d.text(
        (552, 420),
        "NOAA GOES ELECTRONS >2 MeV",
        font=F_XS,
        fill=MUTED
    )

    if DATA["proton"] is not None:
        d.text(
            (322, 625),
            val(DATA["proton"], 2, " pfu"),
            font=F_XS,
            fill=CYAN
        )

    if DATA["electron"] is not None:
        d.text(
            (552, 625),
            val(DATA["electron"], 2, " e/(cm²·s·sr)"),
            font=F_XS,
            fill=PURPLE
        )

    # Alert panel — only messages issued in the last 24 hours
    alert = DATA["alert"]

    d.rounded_rectangle(
        (785, 555, 1248, 625),
        radius=5,
        fill=(25, 8, 12),
        outline=RED,
        width=1
    )

    if alert == "NO CURRENT ALERTS":
        d.text(
            (805, 574),
            "NO CURRENT NOAA ALERTS",
            font=F_M,
            fill=GREEN
        )
    else:
        d.text(
            (800, 565),
            "NOAA",
            font=F_S,
            fill=RED
        )

        words = alert.split()
        line = ""
        lines = []

        for word in words:
            if len(line) + len(word) + 1 > 58:
                lines.append(line)
                line = word
            else:
                line = (line + " " + word).strip()

        if line:
            lines.append(line)

        for i, line in enumerate(lines[:2]):
            d.text(
                (850, 558 + i * 22),
                line,
                font=F_XS,
                fill=WHITE
            )

        if DATA["alert_time"]:
            d.text(
                (850, 602),
                f"Issued {DATA['alert_time']}",
                font=F_XS,
                fill=MUTED
            )

    # Footer status
    d.rectangle((0,660,W,H),fill=(4,10,20))
    d.text((15,675),"SPACE WEATHER NEWS",font=F_S,fill=RED)
    d.text((205,675),f"{status} • Solar wind {val(DATA['speed'],0,' km/s')} • Bz {val(DATA['bz'],1,' nT')}",
           font=F_XS,fill=WHITE)
    d.text((875,675),"NOAA SWPC • LIVE DATA",font=F_XS,fill=GREEN)
    d.text((1090,675),"● LIVE 24/7",font=F_S,fill=GREEN)
    return img

def stop(*_):
    global running
    running=False

signal.signal(signal.SIGINT,stop)
signal.signal(signal.SIGTERM,stop)

def main():
    if not YOUTUBE_KEY:
        print("ERROR: YOUTUBE_KEY GitHub secret is missing.",flush=True)
        sys.exit(1)

    threading.Thread(target=worker,daemon=True).start()
    time.sleep(3)

    stream_url=YOUTUBE_RTMP.rstrip("/")+"/"+YOUTUBE_KEY
    cmd=["ffmpeg","-hide_banner","-loglevel","info",
         "-re",
         "-f","rawvideo","-pix_fmt","rgb24","-s",f"{W}x{H}","-r",str(FPS),"-i","-",
         "-f","lavfi","-i","anullsrc=channel_layout=stereo:sample_rate=44100",
         "-map","0:v:0","-map","1:a:0",
         "-c:v","libx264","-preset","veryfast","-tune","zerolatency",
         "-pix_fmt","yuv420p","-b:v","3500k","-maxrate","4000k","-bufsize","7000k",
         "-g",str(FPS*2),"-keyint_min",str(FPS*2),"-sc_threshold","0",
         "-c:a","aac","-b:a","128k","-ar","44100","-ac","2",
         "-f","flv",stream_url]

    while running:
        print("Starting FFmpeg -> YouTube",flush=True)
        p=subprocess.Popen(cmd,stdin=subprocess.PIPE)
        n=0
        try:
            while running and p.poll() is None:
                p.stdin.write(np.asarray(draw(n),dtype=np.uint8).tobytes())
                n+=1
        except (BrokenPipeError,OSError) as e:
            print("FFmpeg stopped:",e,flush=True)
        finally:
            try:p.stdin.close()
            except:pass
            try:p.wait(timeout=8)
            except: p.kill()
        if running:
            print("Restarting in 10 seconds...",flush=True)
            time.sleep(10)

if __name__=="__main__":
    main()
