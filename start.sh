#!/bin/sh
set -eu

echo "=========================================="
echo "SPACE WEATHER LIVE - 5 DASHBOARD MODE"
echo "Dashboards rotate every 2 minutes"
echo "=========================================="

while true
do
  python /app/space_weather_live.py || true
  echo "Stream exited. Restarting in 10 seconds..."
  sleep 10
done
