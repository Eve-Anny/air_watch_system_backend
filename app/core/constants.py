"""
WHO guideline values, rolling-window definitions, and banding thresholds — matches ARCHITECTURE.md
§4 and its confirmed decisions in §7. These are the single source of truth for the scoring engine
and alert engine; don't duplicate these numbers elsewhere.
"""

from datetime import timedelta

# --- WHO guideline values (WHO Global Air Quality Guidelines, 2021) ---------------------------
CO_1H_GUIDELINE_MG_M3 = 35.0        # primary CO banding input (ARCHITECTURE.md §7, confirmed)
CO_15MIN_GUIDELINE_MG_M3 = 100.0    # fast-spike Critical override only, not used for banding
PM2_5_24H_GUIDELINE_UG_M3 = 15.0
PM10_24H_GUIDELINE_UG_M3 = 45.0

# --- Rolling windows used for WHO comparison (ARCHITECTURE.md §4.1) ----------------------------
ROLLING_WINDOW_CO_PRIMARY = timedelta(hours=1)
ROLLING_WINDOW_CO_SPIKE = timedelta(minutes=15)
ROLLING_WINDOW_PM2_5 = timedelta(hours=24)
ROLLING_WINDOW_PM10 = timedelta(hours=24)
ROLLING_WINDOW_VOC = timedelta(hours=1)

# --- Category bands, as ratio-to-guideline (ARCHITECTURE.md §4.3) ------------------------------
# Ordered (upper_bound_inclusive, band) pairs; anything above the last bound is Hazardous.
CATEGORY_BANDS: list[tuple[float, str]] = [
    (1.0, "Good"),
    (2.0, "Moderate"),
    (4.0, "Unhealthy"),
]
CATEGORY_HAZARDOUS = "Hazardous"

# --- VOC index bands (non-WHO, IAQ-proxy scale; ARCHITECTURE.md §4.3) --------------------------
VOC_BANDS: list[tuple[float, str]] = [
    (100, "Good"),
    (200, "Moderate"),
    (400, "Unhealthy"),
]
VOC_HAZARDOUS = "Hazardous"

# --- Alert level mapping (ARCHITECTURE.md §5) ---------------------------------------------------
# Generalized across all four pollutants (co, pm2_5, pm10, voc) — see services/alert_engine.py's
# module docstring for why VOC isn't treated as a special case here.
ALERT_LEVEL_BY_BAND: dict[str, str | None] = {
    "Good": None,
    "Moderate": "Info",
    "Unhealthy": "Warning",
    "Hazardous": "Critical",
}
