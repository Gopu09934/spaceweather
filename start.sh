#!/bin/sh
set -eu

while true
do
  echo "Starting NOAA Space Weather Live..."
  python /app/space_weather_live.py || true
  echo "Stream exited. Restarting in 10 seconds..."
  sleep 10
done
