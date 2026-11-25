from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional

from pipeline.src.http_client import PoliteHttpClient

logger = logging.getLogger(__name__)

COMPASS_TO_DEG = {
    "N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5,
    "E": 90, "ESE": 112.5, "SE": 135, "SSE": 157.5,
    "S": 180, "SSW": 202.5, "SW": 225, "WSW": 247.5,
    "W": 270, "WNW": 292.5, "NW": 315, "NNW": 337.5,
}


def deg_to_compass(deg: float) -> str:
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    idx = int(((deg % 360) / 22.5) + 0.5) % 16
    return dirs[idx]


def compute_wind_straight_impact(
    wind_from_deg: float,
    wind_speed_kmh: float,
    home_straight_bearing: float,
) -> Dict[str, Any]:
    """
    Compute headwind / tailwind / crosswind component along the track's home straight.
    wind_from_deg is meteorological direction the wind blows FROM (e.g. 180 = Southerly blowing toward N).
    home_straight_bearing is the compass direction runners travel toward the finish post.
    """
    # Wind blows TOWARD (wind_from_deg + 180) % 360.
    # If wind blows FROM the same bearing runners travel toward, it is a direct HEADWIND.
    diff_rad = math.radians(wind_from_deg - home_straight_bearing)
    headwind_component = wind_speed_kmh * math.cos(diff_rad)  # >0 = headwind, <0 = tailwind
    crosswind_component = abs(wind_speed_kmh * math.sin(diff_rad))

    if wind_speed_kmh < 10:
        effect = "Calm"
        pace_bias = "Neutral"
    elif headwind_component >= 12:
        effect = "Strong Headwind"
        pace_bias = "Favours off-pace sitters & cover; punishes exposed leaders"
    elif headwind_component >= 6:
        effect = "Moderate Headwind"
        pace_bias = "Slight edge to horses with cover over exposed leaders"
    elif headwind_component <= -12:
        effect = "Strong Tailwind"
        pace_bias = "Favours leaders & on-pace runners carrying momentum"
    elif headwind_component <= -6:
        effect = "Moderate Tailwind"
        pace_bias = "Mild advantage to on-pace runners"
    elif crosswind_component >= 14:
        effect = "Strong Crosswind"
        pace_bias = "Favours runners with shelter on the protected rail"
    else:
        effect = "Light Breeze"
        pace_bias = "Minimal wind bias"

    return {
        "wind_speed_kmh": round(wind_speed_kmh, 1),
        "wind_from_deg": round(wind_from_deg % 360, 1),
        "wind_compass": deg_to_compass(wind_from_deg),
        "home_straight_bearing": home_straight_bearing,
        "headwind_kmh": round(headwind_component, 1),
        "crosswind_kmh": round(crosswind_component, 1),
        "effect": effect,
        "pace_bias": pace_bias,
    }


class WeatherSource:
    """Fetches BOM hourly forecast with automatic Open-Meteo BOM ACCESS-G fallback."""

    def __init__(self, http: PoliteHttpClient) -> None:
        self.http = http

    def fetch_track_weather(self, track_meta: Dict[str, Any]) -> Dict[str, Any]:
        geohash = track_meta.get("bom_geohash")
        lat = float(track_meta.get("lat", -33.8688))
        lon = float(track_meta.get("lon", 151.2093))
        bearing = float(track_meta.get("home_straight_bearing", 0))

        # 1) Try native BOM hourly JSON endpoint
        if geohash:
            try:
                bom_url = f"https://api.weather.bom.gov.au/v1/locations/{geohash}/forecasts/hourly"
                data = self.http.get_json(bom_url)
                entries = (data or {}).get("data") or []
                if entries:
                    # Select midday/afternoon racing window entry (around index 3-5)
                    slot = entries[min(3, len(entries) - 1)]
                    wind = slot.get("wind") or {}
                    speed = float(wind.get("speed_kilometre") or 0.0)
                    compass = (wind.get("direction") or "N").upper()
                    wind_deg = float(COMPASS_TO_DEG.get(compass, 0.0))
                    rain = slot.get("rain") or {}
                    rain_chance = int(rain.get("chance") or 0)
                    rain_mm = float((rain.get("amount") or {}).get("max") or 0.0)
                    temp = float(slot.get("temp") or 20.0)
                    wind_impact = compute_wind_straight_impact(wind_deg, speed, bearing)
                    return {
                        "source": "BOM Hourly API",
                        "temperature_c": round(temp, 1),
                        "rain_chance_pct": rain_chance,
                        "rain_expected_mm": round(rain_mm, 1),
                        "summary": f"{temp:.0f}°C, {rain_chance}% rain ({rain_mm:.1f}mm), {speed:.0f} km/h {compass}",
                        **wind_impact,
                    }
            except Exception as exc:
                logger.warning("BOM native API unavailable for %s (%s); falling back to Open-Meteo BOM ACCESS-G", geohash, exc)

        # 2) Fallback: Open-Meteo BOM ACCESS-G (zero-key, never blocks GitHub Actions runners)
        try:
            om_url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m,precipitation,precipitation_probability,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
                "models": "bom_access_global",
                "forecast_days": 1,
                "timezone": "Australia/Sydney",
            }
            data = self.http.get_json(om_url, params=params) or {}
            hourly = data.get("hourly") or {}
            temps = hourly.get("temperature_2m") or []
            precs = hourly.get("precipitation") or []
            probs = hourly.get("precipitation_probability") or []
            speeds = hourly.get("wind_speed_10m") or []
            dirs = hourly.get("wind_direction_10m") or []

            # Pick ~14:00 local race-afternoon slot if available
            idx = min(14, max(0, len(temps) - 1))
            temp = float(temps[idx]) if temps else 20.0
            rain_mm = round(sum(float(x or 0.0) for x in precs[8:19]), 1) if precs else 0.0
            rain_chance = int(max((float(x or 0.0) for x in probs[8:19]), default=0.0)) if probs else (60 if rain_mm >= 1.0 else 10)
            speed = float(speeds[idx]) if speeds else 10.0
            wind_deg = float(dirs[idx]) if dirs else 0.0
            wind_impact = compute_wind_straight_impact(wind_deg, speed, bearing)
            return {
                "source": "Open-Meteo (BOM ACCESS-G)",
                "temperature_c": round(temp, 1),
                "rain_chance_pct": rain_chance,
                "rain_expected_mm": rain_mm,
                "summary": f"{temp:.0f}°C, {rain_mm:.1f}mm forecast, {speed:.0f} km/h {wind_impact['wind_compass']}",
                **wind_impact,
            }
        except Exception as exc:
            logger.error("Weather fallback failed for lat=%s lon=%s: %s", lat, lon, exc)
            return {
                "source": "Unavailable",
                "temperature_c": None,
                "rain_chance_pct": 0,
                "rain_expected_mm": 0.0,
                "wind_speed_kmh": 0.0,
                "wind_from_deg": 0.0,
                "wind_compass": "N/A",
                "home_straight_bearing": bearing,
                "headwind_kmh": 0.0,
                "crosswind_kmh": 0.0,
                "effect": "No data available",
                "pace_bias": "Neutral",
                "summary": "No weather data available",
            }
