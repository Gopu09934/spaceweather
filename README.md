# Space Weather LIVE — 6 Dashboard + Music Edition

24/7 NOAA space-weather dashboard for YouTube LIVE.

## Dashboard rotation

Six dashboards rotate every 2 minutes without restarting FFmpeg or the YouTube stream:

1. Space Weather LIVE
2. Solar Activity Monitor
3. Solar Wind Monitor
4. Aurora & Geomagnetic
5. NOAA Space Weather Center
6. Space Weather Monitor — premium documentary style (real SDO magnetogram/continuum imagery, real NOAA sunspot-cycle history chart, brushed-metal title bar, scrolling news ticker)

A 1-second visual crossfade is used when the dashboard changes.

## YouTube secret

Create a GitHub Actions repository secret:

`YOUTUBE_KEY`

## Background music — multiple URLs

Create a GitHub Actions **Repository Variable** named `MUSIC_URLS` (recommended), or a Repository Secret with the same name.

Example:

```text
https://github.com/Gopu09934/mars-vid/releases/download/yg/music.mp3,https://github.com/Gopu09934/mars-vid/releases/download/yg/music2.mp3,https://github.com/Gopu09934/mars-vid/releases/download/yg/music3.mp3
```

Separate URLs with commas, spaces, semicolons, or newlines. Every URL must be a direct HTTP(S) audio URL. Do not concatenate two URLs without a separator.

At startup the container:

1. downloads every track;
2. converts every track to the same AAC/44.1 kHz/stereo format;
3. builds an FFmpeg concat playlist;
4. merges the complete playlist into one normalized audio file and loops that file continuously.

The playlist is created **before FFmpeg starts**, so if music cannot be downloaded you will see the exact `[MUSIC ERROR]` message in the GitHub Actions log. If no usable track exists, the stream falls back to silent audio.

Music continues across dashboard changes because FFmpeg is not restarted when the 2-minute dashboard changes.

### Required GitHub Actions setup

**Recommended:**

Repository → Settings → Secrets and variables → Actions → Variables → New repository variable

```text
Name: MUSIC_URLS
Value: URL1,URL2,URL3
```

The workflow also accepts `MUSIC_URLS` as a repository secret, which is useful if you prefer not to expose the URLs in repository variables.

## Docker

```bash
docker build -t space-weather-live .
docker run --rm \
  -e YOUTUBE_KEY="YOUR_STREAM_KEY" \
  -e MUSIC_URLS="URL1,URL2,URL3" \
  space-weather-live
```


### Dashboard rotation
All six dashboards remain dynamic: live UTC clock, animated visual indicators, scanning graph markers, moving particles/aurora effects, and continuously refreshed NOAA data. Decorative motion does not replace or alter NOAA measurements.

## Real imagery — Sun, Earth & SDO magnetogram/continuum

A background thread refreshes real photos every 10 minutes and drops them straight into the panels, circle-masked to fit:

- **Sun** (Dashboards 1, 2) — NASA SDO, latest AIA 171Å full-disk image (`sdo.gsfc.nasa.gov`), no key required.
- **Earth** (Dashboard 4) — NASA EPIC/DSCOVR, latest natural-color full-disk image (`epic.gsfc.nasa.gov`), no key required.
- **Magnetogram + continuum** (Dashboard 6) — NASA SDO HMI, latest line-of-sight magnetogram and visible-light continuum images, no key required.

The animated glow rings, flare beam, and aurora oval are still drawn live on top of the photos. If any fetch fails (network hiccup, source down), that panel silently falls back to the original procedural drawing — the stream never stalls or crashes waiting on imagery.

## Dashboard 6 — documentary style

A denser, "broadcast documentary" layout inspired by real space-weather monitor overlays:

- Brushed-metal gradient title bar with a bundled condensed display font (`assets/BebasNeue.ttf`, OFL-licensed).
- Gold corner-bracket panels instead of rounded boxes.
- Sunspot Activity panel: current NOAA sunspot number, trailing 12-month average, and year-over-year % change, all computed from NOAA's real `observed-solar-cycle-indices.json` feed (updated every 10 minutes).
- Solar Dynamics Observatory panel: real HMI magnetogram + continuum images side by side.
- Sunspot Historical panel: a real monthly sunspot-number chart going back ~28 years, sourced from the same NOAA feed.
- Synoptic Map panel: a stylized, illustrative solar synoptic-style chart (not a literal reproduction of the official NOAA SWPC hand-drawn synoptic analysis, which isn't published as a simple fetchable image).
- Bottom ticker bar with a channel logo, scrolling live notices (solar wind speed, Kp, X-ray class, alerts), and a UTC clock.

Set the ticker/logo channel name via an env var:

```text
CHANNEL_NAME=YOUR CHANNEL NAME
```

Defaults to `SPACE WEATHER LIVE` if unset.
