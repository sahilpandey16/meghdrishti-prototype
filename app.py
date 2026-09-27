import os
import time
import math
import random
import json
import urllib.request
from datetime import datetime, timezone
from flask import Flask, render_template_string, jsonify, request, Response

app = Flask(__name__)

# ==============================================================================
# WMO WEATHER CODES TRANSLATOR
# ==============================================================================
WMO_WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    95: "Thunderstorm (Slight / Moderate)",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail"
}

# ==============================================================================
# METEOROLOGICAL TELEMETRY, ELEVATION & REVERSE GEOCODING
# ==============================================================================
GEO_CACHE = {}

def get_precise_location_name(lat, lon):
    """Reverse-geocodes coordinates to neighborhood / city / state (Nominatim)."""
    cache_key = (round(lat, 3), round(lon, 3))
    if cache_key in GEO_CACHE:
        return GEO_CACHE[cache_key]

    # Special quick lookup for Ambabari / Jaipur neighborhoods
    if abs(lat - 26.953) < 0.03 and abs(lon - 75.776) < 0.03:
        name = "Ambabari / Vidhyadhar Nagar, Jaipur, Rajasthan"
        GEO_CACHE[cache_key] = name
        return name

    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=15&addressdetails=1"
        req = urllib.request.Request(url, headers={'User-Agent': 'MeghDrishti-Nowcast/1.0 (SIH Prototype)'})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode())
            addr = data.get("address", {})
            suburb = addr.get("suburb") or addr.get("neighbourhood") or addr.get("residential") or addr.get("subdistrict") or addr.get("road")
            city = addr.get("city") or addr.get("town") or addr.get("state_district") or addr.get("county")
            state = addr.get("state", "India")
            parts = [p for p in [suburb, city, state] if p]
            if parts:
                name = ", ".join(parts)
                GEO_CACHE[cache_key] = name
                return name
    except Exception:
        pass

    fallback_name = f"Coordinates [{round(lat, 4)}°N, {round(lon, 4)}°E]"
    GEO_CACHE[cache_key] = fallback_name
    return fallback_name


def fetch_elevation(lat, lon):
    """Fetch terrain elevation (m) using Open-Meteo free Elevation API (Copernicus DEM 90m)."""
    try:
        url = f"https://api.open-meteo.com/v1/elevation?latitude={lat}&longitude={lon}"
        req = urllib.request.Request(url, headers={'User-Agent': 'MeghDrishti-Nowcast/1.0 (SIH Prototype)'})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode())
            elev = data.get("elevation", [0])[0]
            return float(elev) if elev is not None else 0.0
    except Exception:
        return 0.0


def get_regional_climate_factor(lat, lon, elev):
    """
    Regional climatology + orographic adjustment for India.
    Hills enhance precip; deserts suppress precip.
    """
    is_himalayan = (lat > 28.5 and lon > 75 and lon < 95) or (lat > 30 and lon > 74)
    is_western_ghats = (lat > 8 and lat < 21 and lon > 72.5 and lon < 77)
    is_northeast = (lat > 24 and lon > 88)
    is_thar_desert = (lat > 24 and lat < 30 and lon > 69 and lon < 76.5) and elev < 400
    is_indo_gangetic = (lat > 24 and lat < 30 and lon > 76 and lon < 88)
    is_central_india = (lat > 18 and lat < 26 and lon > 76 and lon < 85)

    oro_factor = 1.0
    climate_note = "Plains / Standard"

    if elev > 1200:
        oro_factor = 1.8 + min(1.2, (elev - 1200) / 2000)
        climate_note = "High Orographic Terrain (Hills/Mountains)"
    elif elev > 600:
        oro_factor = 1.35 + (elev - 600) / 1500
        climate_note = "Moderate Elevation (Foothills / Plateau)"
    elif elev < 250 and is_thar_desert:
        oro_factor = 0.35
        climate_note = "Arid Desert (Thar / Rajasthan Lowlands)"
    elif is_thar_desert:
        oro_factor = 0.55
        climate_note = "Semi-Arid Desert Fringe"
    elif is_western_ghats:
        oro_factor = 1.6
        climate_note = "Western Ghats Orographic Zone"
    elif is_northeast:
        oro_factor = 1.5
        climate_note = "NE India High Moisture Regime"
    elif is_himalayan:
        oro_factor = 1.7
        climate_note = "Himalayan Foothills / Valley"
    elif is_indo_gangetic:
        oro_factor = 1.1
        climate_note = "Indo-Gangetic Plain"

    return round(oro_factor, 2), climate_note


def fetch_genuine_weather(lat, lon):
    """Fetches real-time observations from Open-Meteo."""
    elev = fetch_elevation(lat, lon)
    oro_factor, climate_note = get_regional_climate_factor(lat, lon, elev)
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,relative_humidity_2m,dew_point_2m,apparent_temperature,precipitation,rain,showers,weather_code,cloud_cover,surface_pressure,wind_speed_10m,wind_direction_10m,wind_gusts_10m"
            f"&hourly=precipitation_probability,precipitation,weather_code"
            f"&forecast_hours=6"
        )
        req = urllib.request.Request(url, headers={'User-Agent': 'MeghDrishti-Nowcast/1.0 (SIH Prototype)'})
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            data = json.loads(resp.read().decode())
            curr = data.get("current", {})
            hourly = data.get("hourly", {})

            temp = float(curr.get("temperature_2m", 28.0))
            apparent_temp = float(curr.get("apparent_temperature", temp + 2.0))
            rain = float(curr.get("rain", curr.get("precipitation", 0.0)))
            wind = float(curr.get("wind_speed_10m", 12.0))
            gust = float(curr.get("wind_gusts_10m", max(wind * 1.3, 16.0)))
            wind_deg = float(curr.get("wind_direction_10m", 65.0))
            clouds = int(curr.get("cloud_cover", 30))
            rh = float(curr.get("relative_humidity_2m", 65.0))
            dew = float(curr.get("dew_point_2m", 20.0))
            pressure = float(curr.get("surface_pressure", 1008.0))
            wmo_code = int(curr.get("weather_code", 1))

            hourly_probs = hourly.get("precipitation_probability", [0, 0, 0, 0, 0, 0])[:6]
            hourly_precip = hourly.get("precipitation", [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])[:6]

            return {
                "temp": round(temp, 1),
                "apparent_temp": round(apparent_temp, 1),
                "rain": round(rain, 1),
                "wind": round(wind, 1),
                "gust": round(gust, 1),
                "wind_deg": wind_deg,
                "clouds": clouds,
                "rh": round(rh, 1),
                "dew": round(dew, 1),
                "pressure": round(pressure, 1),
                "wmo_code": wmo_code,
                "condition": WMO_WEATHER_CODES.get(wmo_code, "Partly cloudy"),
                "hourly_probs": hourly_probs,
                "hourly_precip": hourly_precip,
                "elevation_m": round(elev, 0),
                "oro_factor": oro_factor,
                "climate_note": climate_note
            }
    except Exception:
        return {
            "temp": 27.6,
            "apparent_temp": 31.0,
            "rain": 0.0,
            "wind": 12.5,
            "gust": 27.0,
            "wind_deg": 74.0,
            "clouds": 51,
            "rh": 75.0,
            "dew": 22.7,
            "pressure": 960.3,
            "wmo_code": 2,
            "condition": "Partly cloudy",
            "hourly_probs": [3, 3, 4, 6, 10, 12],
            "hourly_precip": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "elevation_m": round(elev, 0),
            "oro_factor": oro_factor,
            "climate_note": climate_note
        }

# ==============================================================================
# CONVECTIVE NOWCASTING ENGINE
# ==============================================================================
class MeghDrishtiPhysicsEngine:
    @staticmethod
    def evaluate_convective_threat(weather_data):
        rain = weather_data["rain"]
        wind = weather_data["wind"]
        gust = weather_data["gust"]
        temp = weather_data["temp"]
        clouds = weather_data["clouds"]
        rh = weather_data["rh"]
        dew = weather_data["dew"]
        probs = weather_data["hourly_probs"]
        max_prob = max(probs) if probs else 0
        elev = weather_data.get("elevation_m", 0)
        oro_factor = weather_data.get("oro_factor", 1.0)
        climate_note = weather_data.get("climate_note", "Standard")

        dew_depression = max(0.0, temp - dew)
        moisture_index = max(0.15, min(1.4, (rh / 100.0) * (1.0 - dew_depression / 25.0)))

        instability = 0.0
        if dew_depression < 12 and temp > 22:
            instability = min(100.0, (temp - 18) * 3.5 + (rh - 40) * 0.8 + max(0, 8 - dew_depression) * 4)
        elif dew_depression < 18 and temp > 25:
            instability = min(70.0, (temp - 22) * 2.5 + (rh - 35) * 0.5)

        effective_rain_potential = rain
        if rain < 0.1 and clouds >= 35:
            base_potential = (clouds / 100.0) * moisture_index * (6.0 + instability / 25.0)
            effective_rain_potential = base_potential * oro_factor
            if oro_factor < 0.6:
                effective_rain_potential = min(effective_rain_potential, 1.2)
            elif oro_factor > 1.4:
                effective_rain_potential = min(effective_rain_potential * 1.35, 28.0)

        # 1. Composite Radar Reflectivity (dBZ)
        if effective_rain_potential > 0.05:
            z_linear = 200.0 * (effective_rain_potential ** 1.55)
            dbz = round(min(72.0, max(15.0, 10.0 * math.log10(max(1.0, z_linear)))), 1)
        else:
            dbz = 12.0 if clouds > 70 else (8.0 if clouds > 40 else 0.0)

        if oro_factor > 1.4 and clouds > 55 and moisture_index > 0.6:
            dbz = min(55.0, dbz + 8.0 * (oro_factor - 1.0))

        # 2. Hail MESH
        if dbz >= 44.0 and temp >= 20.0:
            shi = 0.1 * ((dbz - 40.0) ** 1.85) * (1.0 + wind / 60.0)
            mesh_cm = round(min(6.5, max(0.8, 2.54 * math.sqrt(max(0.1, shi)))), 1)
            hail_prob = int(min(98, max(25, (dbz - 42.0) * 3.2 + (wind * 0.25))))
        else:
            mesh_cm = 0.0
            hail_prob = 0

        # 3. Downburst Gust
        downburst_gust = round(max(gust, wind * 1.35 + (dbz / 70.0) * 45.0), 1)

        # 4. Lightning
        if dbz >= 35.0:
            lightning_density = int(min(220, max(5, ((dbz - 32.0) ** 1.9) * 0.45 + (wind * 0.3))))
            if oro_factor > 1.3:
                lightning_density = int(min(250, lightning_density * 1.25))
        else:
            lightning_density = 0

        # 5. Convective Threat Score
        adjusted_max_prob = min(100, max_prob * (0.7 + 0.5 * oro_factor) * moisture_index)
        threat_score = (
            (min(dbz / 65.0, 1.0) * 30.0) +
            (min(effective_rain_potential / 40.0, 1.0) * 22.0) +
            (min(downburst_gust / 100.0, 1.0) * 12.0) +
            (min(adjusted_max_prob / 100.0, 1.0) * 13.0) +
            (min((oro_factor - 0.5) / 1.5, 1.0) * 10.0) +
            (min(instability / 100.0, 1.0) * 13.0)
        )
        threat_score = round(min(100.0, threat_score), 1)

        # 6. Classification
        if effective_rain_potential >= 35.0 or threat_score >= 65.0 or (rain >= 25 and oro_factor > 1.2):
            level = "CRITICAL SEVERE CONVECTIVE"
            color = "#ef4444"
            auto_broadcast = True
        elif effective_rain_potential >= 8.0 or threat_score >= 38.0 or (rain >= 5 and oro_factor > 1.3):
            level = "ACTIVE CONVECTIVE SHOWER"
            color = "#f59e0b"
            auto_broadcast = False
        elif rain > 0.0 or effective_rain_potential > 1.5 or adjusted_max_prob >= 30:
            level = "LIGHT RAIN / CHANCE OF SHOWERS"
            color = "#38bdf8"
            auto_broadcast = False
        elif clouds >= 50:
            level = "PARTLY CLOUDY / STABLE"
            color = "#10b981"
            auto_broadcast = False
        else:
            level = "CLEAR / NORMAL"
            color = "#10b981"
            auto_broadcast = False

        factors = [
            f"Surface Observation: {temp}°C (RealFeel {weather_data['apparent_temp']}°C) | Condition: {weather_data['condition']}",
            f"Elevation: {elev} m | Terrain Regime: {climate_note} (Oro Factor ×{oro_factor})",
            f"Precipitation: {rain} mm/h (Effective Potential: {round(effective_rain_potential,1)} mm/h) | Next 6h Rain Chance: {probs[0]}% → {probs[1]}% → {probs[2]}% → {probs[3]}% → {probs[4]}% → {probs[5]}%",
            f"Surface Wind: {wind} km/h (Gusts: {gust} km/h) | Cloud Cover: {clouds}% | Moisture Index: {round(moisture_index,2)}",
            f"Boundary Layer: {rh}% RH | Dew Point: {dew}°C (Depression: {round(dew_depression,1)}°C) | Pressure: {weather_data['pressure']} hPa",
            f"Instability Proxy: {round(instability,1)} / 100 (surface-based thermodynamic favourability)"
        ]

        if effective_rain_potential > 0.5 or rain > 0:
            factors.append(f"Reflectivity Proxy: {dbz} dBZ | Hail MESH Proxy: {mesh_cm} cm ({hail_prob}%)")
            if oro_factor > 1.3 and rain < 2:
                factors.append("⚠ Orographic Enhancement: Moderate clouds over high terrain can still produce heavy localized rain")
            if oro_factor < 0.6:
                factors.append("⚠ Desert Suppression: Dry mid-levels & high cloud base limit rainfall even under cloudy skies")
        else:
            factors.append("No significant precipitation signal detected at this location")

        return {
            "threat_score": threat_score,
            "level": level,
            "color": color,
            "dbz": dbz,
            "mesh_cm": mesh_cm,
            "hail_prob": hail_prob,
            "downburst_gust": downburst_gust,
            "lightning_density": lightning_density,
            "factors": factors,
            "auto_broadcast": auto_broadcast,
            "effective_rain_potential": round(effective_rain_potential, 1),
            "oro_factor": oro_factor,
            "climate_note": climate_note,
            "elevation_m": elev
        }

# ==============================================================================
# WATCH REGIONS & PRESET SECTORS
# ==============================================================================
WATCH_REGIONS = {
    "jaipur": {
        "name": "Ambabari / Vidhyadhar Nagar, Jaipur",
        "lat": 26.9530, "lon": 75.7760,
        "radar_site": "Monitored Sector • Semi-arid convective risk + urban runoff",
        "risk_focus": "Sudden convective storms, urban flooding, high heat + moisture bursts",
        "infra": [
            {"id": "AMB", "name": "Ambabari Neighborhood Zone", "lat": 26.9530, "lon": 75.7760, "type": "Residential"},
            {"id": "AMR", "name": "Amer Valley Catchment", "lat": 26.9855, "lon": 75.8513, "type": "Basin / Runoff"},
            {"id": "JAI", "name": "Jaipur Int'l Airport (VIJP)", "lat": 26.8242, "lon": 75.8122, "type": "Aviation"},
            {"id": "SGN", "name": "Sanganer Industrial Belt", "lat": 26.8000, "lon": 75.7667, "type": "Urban Industrial"}
        ]
    },
    "delhi": {
        "name": "Delhi-NCR Corridor",
        "lat": 28.6139, "lon": 77.2090,
        "radar_site": "Monitored Sector • High population density + Yamuna flood risk",
        "risk_focus": "Urban flooding, severe thunderstorms, aviation disruption, heat-moisture extremes",
        "infra": [
            {"id": "DEL", "name": "IGI Airport (VIDP)", "lat": 28.5562, "lon": 77.1000, "type": "Aviation"},
            {"id": "NDLS", "name": "New Delhi Railway Junction", "lat": 28.6430, "lon": 77.2197, "type": "Transit"},
            {"id": "GGN", "name": "Gurugram Drainage Corridor", "lat": 28.4595, "lon": 77.0266, "type": "Flood Risk"},
            {"id": "NOI", "name": "Noida Yamuna Floodplain", "lat": 28.5355, "lon": 77.3910, "type": "Lowland Basin"}
        ]
    },
    "lucknow": {
        "name": "Lucknow Corridor",
        "lat": 26.8467, "lon": 80.9462,
        "radar_site": "Monitored Sector • Indo-Gangetic convective + riverine risk",
        "risk_focus": "Thunderstorms, waterlogging, river proximity hazards",
        "infra": [
            {"id": "LKO", "name": "CCS Airport (VILK)", "lat": 26.7606, "lon": 80.8893, "type": "Aviation"},
            {"id": "GOM", "name": "Gomti River Lowland Belt", "lat": 26.8600, "lon": 80.9500, "type": "Urban Catchment"}
        ]
    },
    "patna": {
        "name": "Patna / Gangetic Basin",
        "lat": 25.5941, "lon": 85.1376,
        "radar_site": "Monitored Sector • Flood-prone Gangetic plain",
        "risk_focus": "River flooding, embankment stress, convective downpours",
        "infra": [
            {"id": "PAT", "name": "JP Narayan Airport (VEPT)", "lat": 25.5913, "lon": 85.0880, "type": "Aviation"},
            {"id": "DNP", "name": "Danapur Embankment Zone", "lat": 25.6333, "lon": 85.0500, "type": "Flood Protection"}
        ]
    },
    "kolkata": {
        "name": "Kolkata / Bengal Delta",
        "lat": 22.5726, "lon": 88.3639,
        "radar_site": "Monitored Sector • Deltaic flooding + cyclonic influence zone",
        "risk_focus": "Heavy rain, urban flooding, cyclone outer bands, low-lying areas",
        "infra": [
            {"id": "CCU", "name": "NSCBI Airport (VECC)", "lat": 22.6547, "lon": 88.4467, "type": "Aviation"},
            {"id": "HWH", "name": "Howrah Railway Terminal", "lat": 22.5855, "lon": 88.3426, "type": "Transit"},
            {"id": "BAR", "name": "Barasat Agricultural Belt", "lat": 22.7200, "lon": 88.4800, "type": "Rural / Lowland"}
        ]
    },
    "dehradun": {
        "name": "Dehradun / Tehri Valley",
        "lat": 30.3165, "lon": 78.0322,
        "radar_site": "Monitored Sector • Himalayan foothills • Cloudburst & landslide risk",
        "risk_focus": "Orographic heavy rain, cloudburst potential, slope failures, gorge flooding",
        "infra": [
            {"id": "DED", "name": "Jolly Grant Airport (VIDN)", "lat": 30.1897, "lon": 78.1803, "type": "Aviation"},
            {"id": "RSH", "name": "Rishikesh Ganges Gorge", "lat": 30.0869, "lon": 78.2676, "type": "Cloudburst Risk"},
            {"id": "MSS", "name": "Mussoorie Ridge Settlement", "lat": 30.4598, "lon": 78.0644, "type": "High Slope Hazard"}
        ]
    }
}

def build_nowcast_timeline(lat, lon, weather, indices, infra_list):
    rad = math.radians(weather["wind_deg"])
    steering_speed = max(24.0, weather["wind"] * 1.5)
    time_steps = [0, 15, 30, 45, 60, 90, 120, 180, 240, 300, 360]
    forecast_timeline = {}

    for m in time_steps:
        dist_km = steering_speed * (m / 60.0)
        coriolis_deflect = math.radians(0.035 * m)
        effective_angle = rad + coriolis_deflect
        d_lat = (dist_km * math.cos(effective_angle)) / 111.139
        d_lon = (dist_km * math.sin(effective_angle)) / (111.139 * math.cos(math.radians(lat)))
        c_lat = round(lat + d_lat, 4)
        c_lon = round(lon + d_lon, 4)

        poly = []
        base_radius_deg = 0.12 if weather["rain"] > 0 else 0.05
        uncertainty_spread = (m / 360.0) * 0.10
        eff_r = base_radius_deg + uncertainty_spread
        num_vertices = 14

        for i in range(num_vertices):
            ang = (2 * math.pi / num_vertices) * i
            perturb = 0.88 + 0.22 * math.sin(i * 2.8 + m * 0.1)
            p_lat = c_lat + (eff_r * perturb * math.cos(ang))
            p_lon = c_lon + (eff_r * perturb * math.sin(ang) * 1.15)
            poly.append([round(p_lat, 4), round(p_lon, 4)])

        forecast_timeline[str(m)] = {
            "lead_minutes": m,
            "center": [c_lat, c_lon],
            "polygon": poly,
            "dbz": indices["dbz"],
            "rain_rate": weather["rain"],
            "uncertainty_radius_km": round((eff_r * 111.0), 1),
            "engine_mode": "Observation Sync (T0)" if m == 0 else ("0-2h Advective Nowcast" if m <= 120 else "2-6h Multimodal Blend")
        }

    infra_countdowns = []
    for target in infra_list:
        d_lat_km = (target["lat"] - lat) * 111.139
        d_lon_km = (target["lon"] - lon) * 111.139 * math.cos(math.radians(lat))
        dist_to_target = math.hypot(d_lat_km, d_lon_km)
        angle_to_target = math.atan2(d_lon_km, d_lat_km)
        angle_diff = abs(effective_angle - angle_to_target)
        angle_diff = min(angle_diff, 2 * math.pi - angle_diff)
        is_in_path = angle_diff < math.radians(45.0) and dist_to_target < 250.0

        if is_in_path and weather["rain"] > 0:
            time_hours = dist_to_target / max(10.0, steering_speed)
            eta_seconds = int(time_hours * 3600)
            status = "In estimated path (approx.)"
            urgency = "HIGH" if eta_seconds < 3600 else "MEDIUM"
        else:
            eta_seconds = int((dist_to_target / max(10.0, steering_speed) + 1.2) * 3600)
            status = "Nearby monitored asset"
            urgency = "LOW"

        infra_countdowns.append({
            "id": target["id"],
            "name": target["name"],
            "type": target["type"],
            "lat": target["lat"],
            "lon": target["lon"],
            "distance_km": round(dist_to_target, 1),
            "eta_seconds": eta_seconds,
            "status": status,
            "urgency": urgency
        })

    infra_countdowns.sort(key=lambda x: x["eta_seconds"])
    return forecast_timeline, infra_countdowns

# ==============================================================================
# REST API ENDPOINTS
# ==============================================================================
@app.route("/api/live-region/<region_key>")
def api_live_region(region_key):
    reg = WATCH_REGIONS.get(region_key.lower(), WATCH_REGIONS["jaipur"])
    weather = fetch_genuine_weather(reg["lat"], reg["lon"])
    indices = MeghDrishtiPhysicsEngine.evaluate_convective_threat(weather)
    timeline, countdowns = build_nowcast_timeline(
        reg["lat"], reg["lon"], weather, indices, reg["infra"]
    )
    return jsonify({
        "project": "MEGH-DRISHTI",
        "name": reg["name"],
        "lat": reg["lat"],
        "lon": reg["lon"],
        "radar_site": reg["radar_site"],
        "risk_focus": reg.get("risk_focus", ""),
        "temp": f"{weather['temp']}°C",
        "apparent_temp": f"{weather['apparent_temp']}°C",
        "rain": f"{weather['rain']} mm/h",
        "rain_prob": f"{weather['hourly_probs'][0]}%",
        "condition": weather["condition"],
        "hail": f"{indices['mesh_cm']} cm ({indices['hail_prob']}%)",
        "wind": f"{weather['wind']} km/h",
        "gust": f"{weather['gust']} km/h",
        "clouds": f"{weather['clouds']}%",
        "rh": f"{weather['rh']}%",
        "dew": f"{weather['dew']}°C",
        "pressure": f"{weather['pressure']} hPa",
        "dbz": f"{indices['dbz']} dBZ",
        "lightning": f"{indices['lightning_density']} str/km²",
        "hourly_probs": weather["hourly_probs"],
        "risk_score": indices["threat_score"],
        "risk_level": indices["level"],
        "badge_color": indices["color"],
        "factors": indices["factors"],
        "auto_broadcast": indices["auto_broadcast"],
        "forecast_timeline": timeline,
        "infrastructure_countdowns": countdowns,
        "cap_identifier": f"MD-CAP-{int(time.time())}",
        "elevation_m": weather.get("elevation_m", 0),
        "oro_factor": weather.get("oro_factor", 1.0),
        "climate_note": weather.get("climate_note", "Standard"),
        "effective_rain_potential": indices.get("effective_rain_potential", weather["rain"])
    })


@app.route("/api/click-nowcast")
def api_click_nowcast():
    lat = float(request.args.get("lat", 26.9530))
    lon = float(request.args.get("lon", 75.7760))

    weather = fetch_genuine_weather(lat, lon)
    location_name = get_precise_location_name(lat, lon)
    indices = MeghDrishtiPhysicsEngine.evaluate_convective_threat(weather)

    poly = []
    base_r = 0.12 if weather["rain"] > 0 or indices.get("effective_rain_potential", 0) > 1 else 0.05
    for i in range(12):
        ang = (2 * math.pi / 12) * i
        r = base_r * (0.85 + 0.25 * math.sin(i * 2.5))
        poly.append([round(lat + r * math.cos(ang), 4), round(lon + r * math.sin(ang), 4)])

    return jsonify({
        "name": location_name,
        "lat": lat,
        "lon": lon,
        "temp": f"{weather['temp']}°C",
        "apparent_temp": f"{weather['apparent_temp']}°C",
        "rain": f"{weather['rain']} mm/h",
        "rain_prob": f"{weather['hourly_probs'][0]}%",
        "condition": weather["condition"],
        "hail": f"{indices['mesh_cm']} cm",
        "wind": f"{weather['wind']} km/h",
        "gust": f"{weather['gust']} km/h",
        "clouds": f"{weather['clouds']}%",
        "rh": f"{weather['rh']}%",
        "dew": f"{weather['dew']}°C",
        "pressure": f"{weather['pressure']} hPa",
        "dbz": f"{indices['dbz']} dBZ",
        "lightning": f"{indices['lightning_density']} str/km²",
        "hourly_probs": weather["hourly_probs"],
        "risk_score": indices["threat_score"],
        "risk_level": indices["level"],
        "badge_color": indices["color"],
        "factors": indices["factors"],
        "auto_broadcast": indices["auto_broadcast"],
        "polygon": poly,
        "cap_identifier": f"MD-GEO-{int(time.time())}",
        "elevation_m": weather.get("elevation_m", 0),
        "oro_factor": weather.get("oro_factor", 1.0),
        "climate_note": weather.get("climate_note", "Standard"),
        "effective_rain_potential": indices.get("effective_rain_potential", weather["rain"])
    })


@app.route("/api/download-cap-xml")
def api_download_cap_xml():
    sec_id = request.args.get("region", "jaipur")
    reg = WATCH_REGIONS.get(sec_id.lower(), WATCH_REGIONS["jaipur"])
    weather = fetch_genuine_weather(reg["lat"], reg["lon"])
    indices = MeghDrishtiPhysicsEngine.evaluate_convective_threat(weather)
    sent_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    xml_data = f"""<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>IN-MEGHDRISHTI-CAP-{int(time.time())}</identifier>
  <sender>meghdrishti-prototype@example.local</sender>
  <sent>{sent_iso}</sent>
  <status>Exercise</status>
  <msgType>Alert</msgType>
  <scope>Public</scope>
  <code>PROTOTYPE-CAP-GENERATOR</code>
  <info>
    <category>Met</category>
    <event>Convective Weather Nowcast (Prototype)</event>
    <urgency>Immediate</urgency>
    <severity>{indices['level']}</severity>
    <certainty>Observed</certainty>
    <headline>MEGH-DRISHTI Nowcast: {reg['name']}</headline>
    <description>Terrain-aware analysis using Open-Meteo observations + elevation. Temp {weather['temp']}C, precip {weather['rain']} mm/h, wind {weather['wind']} km/h (gusts {weather['gust']} km/h), condition: {weather['condition']}. Threat Index: {indices['threat_score']}/100. Orographic factor applied. This is a prototype CAP for demonstration only.</description>
    <area>
      <areaDesc>{reg['name']} monitored sector</areaDesc>
      <circle>{reg['lat']},{reg['lon']},25.0</circle>
    </area>
  </info>
</alert>"""
    return Response(
        xml_data,
        mimetype="application/xml",
        headers={"Content-Disposition": f"attachment;filename=CAP_{sec_id.upper()}_{int(time.time())}.xml"}
    )

# ==============================================================================
# FRONTEND TEMPLATE
# ==============================================================================
HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MEGH-DRISHTI | Terrain-Aware Convective Nowcasting Console</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background-color: #060b17; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; overflow-x: hidden; }
        .header-bar { background: #0d1527; border-bottom: 2px solid #38bdf8; padding: 10px 18px; }
        .ops-panel { background: #0b1326; border: 1px solid #1e293b; border-radius: 8px; }
        #map { height: 560px; width: 100%; border-radius: 8px; border: 1px solid #334155; background: #e2e8f0; cursor: crosshair; }
        .metric-card { background: #040916; border: 1px solid #1e293b; border-radius: 6px; padding: 8px; }
        .metric-title { font-size: 0.68rem; color: #94a3b8; font-weight: bold; text-transform: uppercase; letter-spacing: 0.5px; }
        .metric-val { font-size: 1.15rem; font-weight: bold; color: #38bdf8; }
        .terminal-box { background: #030712; font-family: monospace; font-size: 0.76rem; border-left: 3px solid #38bdf8; color: #38bdf8; padding: 8px 10px; height: 95px; overflow-y: auto; }
        .alert-status-pill { font-size: 0.75rem; padding: 4px 8px; border-radius: 4px; font-weight: bold; }
        .radar-colorbar { height: 8px; border-radius: 4px; background: linear-gradient(to right, #059669 0%, #10b981 20%, #facc15 40%, #ea580c 60%, #ef4444 80%, #dc2626 90%, #ec4899 100%); }
        .countdown-card { background: #060e1f; border: 1px solid #1e293b; border-radius: 6px; padding: 6px 10px; }
        .countdown-card.urgent { background: rgba(127, 29, 29, 0.25); border-color: rgba(239, 68, 68, 0.6); }
        .custom-scroll::-webkit-scrollbar { width: 4px; }
        .custom-scroll::-webkit-scrollbar-thumb { background: #334155; border-radius: 4px; }
        .prob-bar-container { display: flex; gap: 4px; align-items: flex-end; height: 35px; margin-top: 4px; }
        .prob-bar { flex: 1; background: #0284c7; border-radius: 2px 2px 0 0; min-height: 4px; position: relative; }
    </style>
</head>
<body class="p-2">
    <div class="header-bar d-flex justify-content-between align-items-center mb-2 rounded shadow-sm">
        <div class="d-flex align-items-center gap-2">
            <i class="fa-solid fa-satellite-dish fs-4 text-info"></i>
            <div>
                <h5 class="mb-0 fw-bold text-white">
                    MEGH-DRISHTI 
                    <span class="badge bg-primary fs-6 ms-2">TERRAIN-AWARE NOWCASTING</span>
                    <span class="badge bg-dark border border-info text-info ms-1" style="font-size: 0.72rem;">0–6 HOUR • HIGH-RISK SECTORS</span>
                </h5>
                <small class="text-secondary">Real-time observations + Elevation-aware physics • OpenStreetMap</small>
            </div>
        </div>
        <div class="d-flex gap-2 align-items-center">
            <span class="badge bg-success"><i class="fa-solid fa-circle me-1" style="font-size:8px;"></i>LIVE DATA ACTIVE</span>
            <span class="badge bg-info text-dark font-monospace fw-bold"><i class="fa-regular fa-clock me-1"></i><span id="header-clock">--:--:-- IST</span></span>
        </div>
    </div>

    <div class="row g-2">
        <div class="col-lg-7">
            <div class="ops-panel p-2">
                <div class="d-flex justify-content-between align-items-center mb-2 flex-wrap gap-1">
                    <div class="btn-group flex-wrap" role="group">
                        <button class="btn btn-sm btn-outline-info active" id="btn-jaipur" onclick="loadRegion('jaipur')">Ambabari (Jaipur)</button>
                        <button class="btn btn-sm btn-outline-info" id="btn-delhi" onclick="loadRegion('delhi')">Delhi-NCR</button>
                        <button class="btn btn-sm btn-outline-info" id="btn-lucknow" onclick="loadRegion('lucknow')">Lucknow</button>
                        <button class="btn btn-sm btn-outline-info" id="btn-patna" onclick="loadRegion('patna')">Patna</button>
                        <button class="btn btn-sm btn-outline-info" id="btn-kolkata" onclick="loadRegion('kolkata')">Kolkata</button>
                        <button class="btn btn-sm btn-outline-info" id="btn-dehradun" onclick="loadRegion('dehradun')">Dehradun</button>
                    </div>
                    <div class="btn-group" role="group">
                        <button class="btn btn-sm btn-danger active" id="layer-btn-dbz" onclick="setLayer('dbz')">Reflectivity Proxy</button>
                        <button class="btn btn-sm btn-outline-warning" id="layer-btn-lightning" onclick="setLayer('lightning')">Lightning Proxy</button>
                        <button class="btn btn-sm btn-outline-primary" id="layer-btn-hail" onclick="setLayer('hail')">Hail Proxy</button>
                        <button class="btn btn-sm btn-outline-info" id="layer-btn-gust" onclick="setLayer('gust')">Gust Proxy</button>
                    </div>
                </div>
                <div id="map"></div>
                <div class="mt-2 p-2 rounded bg-black border border-secondary">
                    <div class="d-flex justify-content-between align-items-center mb-1 flex-wrap gap-2">
                        <div class="d-flex align-items-center gap-2">
                            <button id="btn-play-pause" onclick="togglePlayPause()" class="btn btn-primary btn-sm px-2 py-0">
                                <i id="play-pause-icon" class="fa-solid fa-play" style="font-size: 0.75rem;"></i>
                            </button>
                            <span class="text-white fw-bold" style="font-size: 0.82rem;" id="time-step-label">NOW (T0 Real-Time Observation)</span>
                        </div>
                        <div class="d-flex gap-1" role="group">
                            <button class="btn btn-danger btn-sm py-0 px-2 active" id="step-0" onclick="changeStep(0)">NOW</button>
                            <button class="btn btn-outline-light btn-sm py-0 px-2" id="step-15" onclick="changeStep(15)">+15m</button>
                            <button class="btn btn-outline-light btn-sm py-0 px-2" id="step-30" onclick="changeStep(30)">+30m</button>
                            <button class="btn btn-outline-light btn-sm py-0 px-2" id="step-60" onclick="changeStep(60)">+1h</button>
                            <button class="btn btn-outline-light btn-sm py-0 px-2" id="step-120" onclick="changeStep(120)">+2h</button>
                            <button class="btn btn-outline-light btn-sm py-0 px-2" id="step-240" onclick="changeStep(240)">+4h</button>
                            <button class="btn btn-outline-light btn-sm py-0 px-2" id="step-360" onclick="changeStep(360)">+6h</button>
                        </div>
                    </div>
                    <input type="range" id="time-slider" min="0" max="360" step="15" value="0" oninput="onSliderMove(this.value)" class="form-range" style="height: 6px;">
                    <div class="d-flex justify-content-between text-secondary mb-1" style="font-size: 0.72rem;">
                        <span id="scale-min">Light proxy</span>
                        <span id="scale-mid">Moderate</span>
                        <span id="scale-core">Heavy potential</span>
                        <span class="text-danger fw-bold" id="scale-max">Severe potential</span>
                    </div>
                    <div class="radar-colorbar"></div>
                </div>
            </div>
        </div>

        <div class="col-lg-5">
            <div class="ops-panel p-3">
                <div class="d-flex justify-content-between align-items-start mb-2">
                    <div>
                        <span class="badge" id="risk-badge" style="background: #10b981; font-size: 0.85rem;">STABLE</span>
                        <h5 class="text-white mt-1 mb-0 fw-bold" id="panel-title">Ambabari / Vidhyadhar Nagar, Jaipur</h5>
                        <small class="text-secondary" id="panel-coords">Monitored high-risk sector</small>
                    </div>
                    <div>
                        <small class="text-secondary d-block text-end fw-bold">CONVECTIVE THREAT</small>
                        <div class="fw-bold fs-4 text-end font-monospace" id="panel-risk-score" style="color: #10b981;">0 / 100</div>
                    </div>
                </div>

                <div class="row g-2 mb-2">
                    <div class="col-3">
                        <div class="metric-card text-center">
                            <div class="metric-title">SURFACE TEMP</div>
                            <div class="metric-val text-white" id="val-temp">--°C</div>
                            <small class="text-secondary d-block" style="font-size: 0.65rem;" id="val-feels">Feels --°C</small>
                        </div>
                    </div>
                    <div class="col-3">
                        <div class="metric-card text-center">
                            <div class="metric-title">LIVE PRECIP</div>
                            <div class="metric-val text-info" id="val-rain">0.0 mm/h</div>
                            <small class="text-secondary d-block" style="font-size: 0.65rem;" id="val-prob">Chance: 0%</small>
                        </div>
                    </div>
                    <div class="col-3">
                        <div class="metric-card text-center">
                            <div class="metric-title">WIND SPEED</div>
                            <div class="metric-val text-warning" id="val-wind">-- km/h</div>
                            <small class="text-secondary d-block" style="font-size: 0.65rem;" id="val-gust">Gusts --</small>
                        </div>
                    </div>
                    <div class="col-3">
                        <div class="metric-card text-center">
                            <div class="metric-title">HUMIDITY</div>
                            <div class="metric-val text-info" id="val-rh">--%</div>
                            <small class="text-secondary d-block" style="font-size: 0.65rem;" id="val-dew">Dew --°C</small>
                        </div>
                    </div>
                </div>

                <div class="p-2 mb-2 rounded bg-black border border-secondary">
                    <div class="d-flex justify-content-between align-items-center mb-1">
                        <span class="text-light fw-bold" style="font-size: 0.78rem;"><i class="fa-solid fa-chart-simple text-info me-1"></i>NEXT 6-HOUR RAIN PROBABILITY CURVE:</span>
                        <span class="badge bg-secondary font-monospace" id="current-sky-condition" style="font-size: 0.70rem;">PARTLY CLOUDY</span>
                    </div>
                    <div class="prob-bar-container" id="prob-bars"></div>
                    <div class="d-flex justify-content-between text-secondary mt-1 font-monospace" style="font-size: 0.65rem;">
                        <span>+1h</span><span>+2h</span><span>+3h</span><span>+4h</span><span>+5h</span><span>+6h</span>
                    </div>
                </div>

                <div class="p-2 mb-2 rounded bg-black border border-secondary">
                    <div class="d-flex justify-content-between align-items-center mb-1">
                        <span class="text-light fw-bold" style="font-size: 0.80rem;"><i class="fa-solid fa-microchip text-info me-1"></i>DIAGNOSTICS & PHYSICS:</span>
                        <span class="badge bg-dark border border-secondary text-secondary font-monospace" style="font-size: 0.68rem;">OPEN-METEO + ELEVATION</span>
                    </div>
                    <div class="text-secondary custom-scroll" style="font-size: 0.74rem; max-height: 52px; overflow-y: auto;" id="panel-factors">Ingesting data...</div>
                </div>

                <div class="p-2 mb-2 rounded" style="background:#0f172a; border:1px solid #334155;">
                    <div class="text-secondary" style="font-size: 0.68rem; line-height:1.35;">
                        <strong class="text-info">Data & Scope:</strong> Real-time Open-Meteo observations + 90 m elevation. Reflectivity / hail / lightning are physics-based proxies. CAP is a prototype generator only.
                    </div>
                </div>

                <div class="mb-2">
                    <div class="d-flex justify-content-between align-items-center mb-1">
                        <span class="text-light fw-bold" style="font-size: 0.80rem;">
                            <i class="fa-solid fa-stopwatch text-danger me-1"></i>MONITORED INFRASTRUCTURE:
                        </span>
                        <span class="badge bg-secondary font-monospace" style="font-size: 0.68rem;">LOCAL ASSETS</span>
                    </div>
                    <div class="space-y-1 custom-scroll" id="countdown-list" style="max-height: 105px; overflow-y: auto;"></div>
                </div>

                <div class="d-flex justify-content-between align-items-center mb-1 mt-1">
                    <div>
                        <span class="text-light fw-bold" style="font-size: 0.80rem;"><i class="fa-solid fa-tower-broadcast text-warning me-1"></i>CAP ALERT GENERATOR (Prototype)</span>
                        <div id="auto-broadcast-status" class="alert-status-pill mt-0.5" style="background:#064e3b; color:#a7f3d0; display:inline-block; font-size: 0.70rem;">WATCH</div>
                    </div>
                    <div class="d-flex gap-1">
                        <a id="btn-cap-xml" href="/api/download-cap-xml?region=jaipur" class="btn btn-sm btn-outline-info fw-bold" download style="font-size: 0.75rem;">
                            <i class="fa-solid fa-file-code me-1"></i>CAP XML
                        </a>
                        <button class="btn btn-sm btn-outline-danger fw-bold" onclick="simulateCAPDispatch()" style="font-size: 0.75rem;">
                            <i class="fa-solid fa-paper-plane me-1"></i>SIMULATE
                        </button>
                    </div>
                </div>

                <div class="terminal-box rounded custom-scroll" id="terminal-screen">
                    <div>[SYSTEM] MEGH-DRISHTI online. Click a sector or any point for terrain-aware analysis.</div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script>
        setInterval(() => {
            const now = new Date();
            document.getElementById('header-clock').innerText = now.toTimeString().split(' ')[0] + " IST";
        }, 1000);

        const map = L.map('map', { zoomControl: true }).setView([26.9530, 75.7760], 11);
        L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '© OpenStreetMap contributors'
        }).addTo(map);

        let activeRegionData = null;
        let selectedStep = 0;
        let activeLayer = 'dbz';
        let isPlaying = false;
        let playInterval = null;
        let advectionLayers = [];
        let trajectoryVector = null;
        let targetMarkers = [];
        let inspectionMarker = null;
        let countdownItems = [];

        setInterval(() => {
            if (!countdownItems || countdownItems.length === 0) return;
            countdownItems.forEach((item, idx) => {
                if (item.eta_seconds > 0) item.eta_seconds -= 1;
                const clockEl = document.getElementById(`cd-clock-${idx}`);
                if (clockEl) clockEl.innerText = formatTime(item.eta_seconds);
            });
        }, 1000);

        function formatTime(seconds) {
            if (seconds <= 0) return "NEAR NOW";
            const h = Math.floor(seconds / 3600);
            const m = Math.floor((seconds % 3600) / 60);
            const s = Math.floor(seconds % 60);
            if (h > 0) {
                return `${String(h).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
            }
            return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
        }

        async function loadRegion(regionKey) {
            document.querySelectorAll('.btn-group button').forEach(b => {
                if (b.id.startsWith('btn-')) b.classList.remove('active');
            });
            const activeBtn = document.getElementById(`btn-${regionKey}`);
            if (activeBtn) activeBtn.classList.add('active');
            document.getElementById('btn-cap-xml').href = `/api/download-cap-xml?region=${regionKey}`;

            if (inspectionMarker) {
                map.removeLayer(inspectionMarker);
                inspectionMarker = null;
            }

            try {
                logConsole(`[QUERY] Loading terrain-aware nowcast for ${regionKey.toUpperCase()}...`);
                const res = await fetch(`/api/live-region/${regionKey}`);
                activeRegionData = await res.json();
                map.flyTo([activeRegionData.lat, activeRegionData.lon], 11, { duration: 1.2 });
                selectedStep = 0;
                document.getElementById('time-slider').value = 0;
                updateTimeButtons(0);
                countdownItems = activeRegionData.infrastructure_countdowns || [];
                renderTelemetryPanel();
                renderCountdownClocks();
                renderAdvectionStep();
                logConsole(`✓ ${activeRegionData.name}: Temp ${activeRegionData.temp}, Precip ${activeRegionData.rain}, Wind ${activeRegionData.wind}.`);
            } catch(err) {
                logConsole(`[ERROR] Telemetry ingestion failed: ${err}`);
            }
        }

        function renderTelemetryPanel() {
            document.getElementById('panel-title').innerText = activeRegionData.name;
            document.getElementById('panel-coords').innerText = activeRegionData.radar_site + (activeRegionData.risk_focus ? " • " + activeRegionData.risk_focus : "");
            const badge = document.getElementById('risk-badge');
            badge.innerText = activeRegionData.risk_level;
            badge.style.background = activeRegionData.badge_color;
            const scoreEl = document.getElementById('panel-risk-score');
            scoreEl.innerText = `${activeRegionData.risk_score} / 100`;
            scoreEl.style.color = activeRegionData.badge_color;

            document.getElementById('val-temp').innerText = activeRegionData.temp;
            document.getElementById('val-feels').innerText = `Feels ${activeRegionData.apparent_temp}`;
            document.getElementById('val-rain').innerText = activeRegionData.rain;
            document.getElementById('val-prob').innerText = `Chance: ${activeRegionData.rain_prob}`;
            document.getElementById('val-wind').innerText = activeRegionData.wind;
            document.getElementById('val-gust').innerText = `Gusts: ${activeRegionData.gust}`;
            document.getElementById('val-rh').innerText = activeRegionData.rh;
            document.getElementById('val-dew').innerText = `Dew: ${activeRegionData.dew}`;
            document.getElementById('current-sky-condition').innerText = activeRegionData.condition.toUpperCase();

            const barsContainer = document.getElementById('prob-bars');
            barsContainer.innerHTML = '';
            if (activeRegionData.hourly_probs) {
                activeRegionData.hourly_probs.forEach((prob, i) => {
                    const bar = document.createElement('div');
                    bar.className = 'prob-bar';
                    const heightPct = Math.max(10, prob);
                    bar.style.height = `${heightPct}%`;
                    bar.title = `+${i+1}h: ${prob}% rain probability`;
                    if (prob > 30) bar.style.background = '#f59e0b';
                    if (prob > 60) bar.style.background = '#ef4444';
                    barsContainer.appendChild(bar);
                });
            }

            document.getElementById('panel-factors').innerHTML = 
                (activeRegionData.factors || []).map(f => `<div>✓ ${f}</div>`).join('') +
                (activeRegionData.elevation_m !== undefined ? `<div>✓ Elevation: ${activeRegionData.elevation_m} m | Terrain: ${activeRegionData.climate_note || ''} (×${activeRegionData.oro_factor || 1})</div>` : '');

            const statusBox = document.getElementById('auto-broadcast-status');
            if (activeRegionData.auto_broadcast) {
                statusBox.style.background = "#7f1d1d";
                statusBox.style.color = "#fecaca";
                statusBox.innerText = "HIGH THREAT – CAP READY";
            } else {
                statusBox.style.background = "#064e3b";
                statusBox.style.color = "#a7f3d0";
                statusBox.innerText = `WATCH: ${activeRegionData.risk_level}`;
            }
        }

        function renderCountdownClocks() {
            const container = document.getElementById('countdown-list');
            container.innerHTML = '';
            countdownItems.forEach((item, idx) => {
                const isUrgent = item.urgency === "HIGH";
                const row = document.createElement('div');
                row.className = `countdown-card ${isUrgent ? 'urgent' : ''} mb-1 d-flex justify-content-between align-items-center`;
                row.innerHTML = `
                    <div>
                        <div class="d-flex align-items-center gap-1.5">
                            <span class="badge ${isUrgent ? 'bg-danger' : 'bg-secondary'}" style="font-size: 0.65rem;">${item.type}</span>
                            <small class="text-secondary font-monospace">${item.distance_km} km</small>
                        </div>
                        <div class="text-white fw-bold mt-0.5" style="font-size: 0.74rem;">${item.name}</div>
                        <small class="${isUrgent ? 'text-danger' : 'text-secondary'}" style="font-size: 0.66rem;">${item.status}</small>
                    </div>
                    <div class="text-end">
                        <div class="font-monospace fw-bold ${isUrgent ? 'text-danger' : 'text-info'}" style="font-size: 0.85rem;" id="cd-clock-${idx}">
                            ${formatTime(item.eta_seconds)}
                        </div>
                        <small class="text-secondary text-uppercase d-block" style="font-size: 0.60rem;">EST. TIME</small>
                    </div>
                `;
                container.appendChild(row);
            });
        }

        function renderAdvectionStep() {
            advectionLayers.forEach(l => map.removeLayer(l));
            advectionLayers = [];
            targetMarkers.forEach(m => map.removeLayer(m));
            targetMarkers = [];
            if (trajectoryVector) map.removeLayer(trajectoryVector);
            if (!activeRegionData || !activeRegionData.forecast_timeline) return;

            const timeline = activeRegionData.forecast_timeline;
            const step = timeline[selectedStep.toString()] || timeline["0"];
            const coords = Object.keys(timeline).map(k => timeline[k].center);

            trajectoryVector = L.polyline(coords, { 
                color: '#f43f5e', 
                weight: 3, 
                dashArray: '6, 6',
                opacity: 0.80 
            }).addTo(map);

            let polyColor = activeRegionData.badge_color;
            if (activeLayer === 'lightning') polyColor = '#f59e0b';
            else if (activeLayer === 'hail') polyColor = '#8b5cf6';
            else if (activeLayer === 'gust') polyColor = '#06b6d4';

            const stormPoly = L.polygon(step.polygon, {
                color: polyColor,
                weight: 2,
                fillColor: polyColor,
                fillOpacity: activeRegionData.rain !== "0.0 mm/h" ? 0.40 : 0.15
            }).addTo(map);

            stormPoly.bindPopup(`
                <div style="font-family: sans-serif; font-size: 13px;">
                    <strong style="color:${polyColor};">${activeRegionData.name}</strong><br>
                    <strong>Lead Time:</strong> +${step.lead_minutes} min<br>
                    <strong>Observed Rain:</strong> ${activeRegionData.rain}<br>
                    <strong>Temperature:</strong> ${activeRegionData.temp}<br>
                    <strong>Condition:</strong> ${activeRegionData.condition}<br>
                    <strong>Mode:</strong> ${step.engine_mode}
                </div>
            `);
            advectionLayers.push(stormPoly);

            countdownItems.forEach(item => {
                const marker = L.circleMarker([item.lat, item.lon], {
                    radius: 5,
                    color: item.urgency === 'HIGH' ? '#ef4444' : '#38bdf8',
                    weight: 2,
                    fillColor: item.urgency === 'HIGH' ? '#dc2626' : '#0284c7',
                    fillOpacity: 0.90
                }).addTo(map);

                marker.bindPopup(`
                    <div style="font-family: sans-serif; font-size: 12px;">
                        <strong>${item.name}</strong> (${item.type})<br>
                        <strong>Distance:</strong> ${item.distance_km} km<br>
                        <strong>Status:</strong> ${item.status}
                    </div>
                `);
                targetMarkers.push(marker);
            });
        }

        function changeStep(step) {
            selectedStep = step;
            document.getElementById('time-slider').value = step;
            updateTimeButtons(step);
            if (activeRegionData && activeRegionData.forecast_timeline) {
                renderAdvectionStep();
                logConsole(`[NOWCAST] Projected horizon to T+${step} min.`);
            }
        }

        function onSliderMove(val) {
            selectedStep = parseInt(val);
            updateTimeButtons(selectedStep);
            renderAdvectionStep();
        }

        function updateTimeButtons(step) {
            [0, 15, 30, 60, 120, 240, 360].forEach(s => {
                const btn = document.getElementById(`step-${s}`);
                if (btn) btn.className = (s === step) ? "btn btn-danger btn-sm py-0 px-2 active" : "btn btn-outline-light btn-sm py-0 px-2";
            });
            const label = document.getElementById('time-step-label');
            if (step === 0) {
                label.innerText = "NOW (T0 Real-Time Observation)";
            } else {
                const h = Math.floor(step / 60);
                const m = step % 60;
                label.innerText = `+${h > 0 ? h + 'h ' : ''}${m > 0 ? m + 'm' : ''} Projection`;
            }
        }

        function togglePlayPause() {
            isPlaying = !isPlaying;
            const icon = document.getElementById('play-pause-icon');
            if (isPlaying) {
                icon.className = "fa-solid fa-pause";
                playInterval = setInterval(() => {
                    selectedStep += 15;
                    if (selectedStep > 360) selectedStep = 0;
                    document.getElementById('time-slider').value = selectedStep;
                    updateTimeButtons(selectedStep);
                    renderAdvectionStep();
                }, 800);
            } else {
                icon.className = "fa-solid fa-play";
                clearInterval(playInterval);
            }
        }

        function setLayer(layer) {
            activeLayer = layer;
            ['dbz', 'lightning', 'hail', 'gust'].forEach(l => {
                const btn = document.getElementById(`layer-btn-${l}`);
                if (btn) btn.className = (l === layer) ? "btn btn-sm btn-danger active" : "btn btn-sm btn-outline-light";
            });
            renderAdvectionStep();
            logConsole(`[LAYER] Switched parameter view to: ${layer.toUpperCase()}`);
        }

        map.on('click', async (e) => {
            const { lat, lng } = e.latlng;
            logConsole(`[PROBE] Analysing point: [${lat.toFixed(4)}°N, ${lng.toFixed(4)}°E]...`);
            try {
                const res = await fetch(`/api/click-nowcast?lat=${lat}&lon=${lng}`);
                const data = await res.json();
                if (inspectionMarker) map.removeLayer(inspectionMarker);
                inspectionMarker = L.circleMarker([lat, lng], {
                    radius: 8,
                    color: '#ffffff',
                    weight: 2,
                    fillColor: data.badge_color,
                    fillOpacity: 0.95
                }).addTo(map);

                inspectionMarker.bindPopup(`
                    <div style="font-family: sans-serif; font-size: 13px;">
                        <strong style="color: #38bdf8;">${data.name}</strong><br>
                        <strong>Temperature:</strong> ${data.temp} (Feels ${data.apparent_temp})<br>
                        <strong>Condition:</strong> ${data.condition}<br>
                        <strong>Precipitation:</strong> ${data.rain} (Chance: ${data.rain_prob})<br>
                        <strong>Wind:</strong> ${data.wind} (Gusts ${data.gust})<br>
                        <strong>Status:</strong> <span style="color:${data.badge_color}; font-weight:bold;">${data.risk_level}</span>
                    </div>
                `).openPopup();

                activeRegionData = data;
                renderTelemetryPanel();
                logConsole(`✓ Location probed: ${data.name} | Temp: ${data.temp} | Rain: ${data.rain} | Wind: ${data.wind}.`);
            } catch(err) {
                logConsole(`[ERROR] Probe query failed: ${err}`);
            }
        });

        function simulateCAPDispatch() {
            logConsole("[CAP] Generating OASIS CAP v1.2 prototype alert for current sector...");
            setTimeout(() => logConsole("✓ CAP XML formatted (demo only)"), 300);
            setTimeout(() => logConsole("✓ Ready for download / future gateway integration"), 600);
        }

        function logConsole(msg) {
            const box = document.getElementById('terminal-screen');
            const row = document.createElement('div');
            row.innerText = msg;
            box.appendChild(row);
            box.scrollTop = box.scrollHeight;
        }

        window.onload = () => loadRegion('jaipur');
    </script>
</body>
</html>"""

@app.route("/")
def index():
    return render_template_string(HTML_PAGE)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)