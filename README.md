# NOAA Space Weather LIVE

Docker + Python + FFmpeg dashboard for YouTube Live.

## Data
- NOAA SWPC planetary Kp
- NOAA real-time solar wind speed/density
- NOAA real-time magnetic field Bz/Bt
- GOES X-ray flux
- GOES integral proton flux (>10 MeV)
- GOES integral electron flux (>2 MeV)
- Recent NOAA alerts (last 24 hours)

## YouTube secret
Create a GitHub Actions repository secret:

`YOUTUBE_KEY`

Do not put the stream key in `start.sh` or source code.

## Layout fixes
- Solar-wind metrics no longer overlap the graph.
- Header LIVE text no longer overlaps the title.
- Footer ticker spacing fixed.
- Particle panels use NOAA GOES feeds instead of simulated values.
- Old NOAA alerts are filtered out after 24 hours.
- Aurora panel no longer presents a Kp-derived percentage as an OVATION probability.
- Recent NOAA history is backfilled into graphs when data is fetched.
