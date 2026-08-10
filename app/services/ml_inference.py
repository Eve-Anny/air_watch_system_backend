"""
ML inference for the /api/v1/forecast endpoint. Loads the Random Forest classifier/forecaster
artifacts trained by ml/train.py from backend/app/ml_models/ — a deployment-local copy (train.py
copies its output here) so this service doesn't depend on the sibling ml/ directory existing at
runtime, e.g. when only backend/ is deployed to Render/Railway.

Rule engine vs. ML relationship (ARCHITECTURE.md §7, confirmed decision): the rule-based WHO scoring
engine (services/scoring_engine.py) is authoritative for real-time category/dominant_pollutant. The
RF classifier here predicts NEXT-HOUR category from the current reading (a secondary/comparative
"near-term outlook" signal, `ml_classification_now` below — not a restatement of the current
instant's rule-engine category, which would be pure label leakage; see ml/README.md §3 for why the
classifier is deliberately trained on a next-hour target). The RF forecaster predicts where the rule
engine's own rolling-average-derived scores are heading over a multi-hour horizon, using labels/bands
generated with the SAME logic as scoring_engine.py (mirrored in ml/src/labeling.py, duplicated here
in _category_and_dominant for the same reason ml/src/labeling.py documents: separate deployments,
no clean shared-import path).

Falls back to a naive persistence placeholder (MODEL_STATUS stays "placeholder") if the trained
artifacts aren't present, or if a device doesn't have enough reading history yet for real features —
e.g. a fresh clone before `python ml/train.py` has been run, or a device's very first reading. This
keeps the API always functional, with or without ML training having been run.
"""

import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ..core import constants as who_constants

# Models are trained on pandas DataFrames (named columns) but served here with plain lists (no
# pandas dependency at inference time, to keep the deployed backend lighter) - sklearn's "X does not
# have valid feature names" warning is expected and harmless as long as column order is preserved,
# which it is (FEATURE_COLUMNS order is fixed at train time and reused identically here).
warnings.filterwarnings("ignore", message="X does not have valid feature names", category=UserWarning)

MODELS_DIR = Path(__file__).resolve().parents[1] / "ml_models"
FORECAST_STEP_HOURS = 1

CLASSIFIER_FEATURES = ["co_1h", "pm2_5_24h", "pm10_24h", "temperature_c", "humidity_pct", "hour_of_day", "month"]
FORECASTER_FEATURES = CLASSIFIER_FEATURES + ["co_mg_m3_lag1", "pm2_5_ug_m3_lag1", "pm10_ug_m3_lag1"]


def _band_for_ratio(ratio: Optional[float]) -> str:
    if ratio is None:
        return "Good"
    for upper_bound, band in who_constants.CATEGORY_BANDS:
        if ratio <= upper_bound:
            return band
    return who_constants.CATEGORY_HAZARDOUS


def _ratios(co_mg_m3, pm2_5_ug_m3, pm10_ug_m3) -> dict:
    """WHO-guideline ratios (dimensionless, comparable across CO/PM2.5/PM10 despite their different
    units) - exposed on forecast steps (`pollutant_ratios`) so the frontend can plot the forecast on
    the same "x guideline" axis the historical trend chart uses, instead of raw mg/m3-vs-ug/m3 values
    that would force a dual-axis chart (a recognized poor practice - see TrendChart.tsx)."""
    return {
        "co": co_mg_m3 / who_constants.CO_1H_GUIDELINE_MG_M3 if co_mg_m3 is not None else None,
        "pm2_5": pm2_5_ug_m3 / who_constants.PM2_5_24H_GUIDELINE_UG_M3 if pm2_5_ug_m3 is not None else None,
        "pm10": pm10_ug_m3 / who_constants.PM10_24H_GUIDELINE_UG_M3 if pm10_ug_m3 is not None else None,
    }


def _category_and_dominant(co_mg_m3, pm2_5_ug_m3, pm10_ug_m3) -> tuple[str, Optional[str]]:
    ratios = _ratios(co_mg_m3, pm2_5_ug_m3, pm10_ug_m3)
    scored = {pollutant: r for pollutant, r in ratios.items() if r is not None}
    if not scored:
        return "Good", None
    dominant = max(scored, key=scored.get)
    return _band_for_ratio(scored[dominant]), dominant


def _load_artifacts():
    try:
        import joblib

        classifier = joblib.load(MODELS_DIR / "classifier.joblib")
        forecasters = {p: joblib.load(MODELS_DIR / f"forecaster_{p}.joblib") for p in ("co", "pm2_5", "pm10")}
        return classifier, forecasters, "trained"
    except FileNotFoundError:
        return None, {}, "placeholder"


_classifier, _forecasters, MODEL_STATUS = _load_artifacts()


def _extract_features(reading: Optional[dict], previous: Optional[dict]) -> Optional[dict]:
    """Builds the feature dict the trained models expect from a Mongo `readings` document (+ the
    previous reading, for lag1). Returns None if the reading doesn't have enough real data yet
    (e.g. sensor failures, or too early in the rolling-window warm-up) - callers fall back to the
    placeholder in that case rather than feeding the model incomplete/fabricated inputs."""
    if not reading:
        return None

    rolling = reading.get("rolling_averages", {})
    raw = reading.get("raw", {})
    timestamp = reading.get("timestamp")

    co = rolling.get("co_mg_m3_1h")
    pm2_5 = rolling.get("pm2_5_ug_m3_24h")
    pm10 = rolling.get("pm10_ug_m3_24h")
    temperature = raw.get("temperature_c")
    humidity = raw.get("humidity_pct")

    if None in (co, pm2_5, pm10, temperature, humidity, timestamp):
        return None

    prev_rolling = (previous or {}).get("rolling_averages", {})
    return {
        "co_1h": co,
        "pm2_5_24h": pm2_5,
        "pm10_24h": pm10,
        "temperature_c": temperature,
        "humidity_pct": humidity,
        "hour_of_day": timestamp.hour,
        "month": timestamp.month,
        # Defaults to the current value if there's no previous reading yet (device's first-ever
        # reading) - a documented "assume no change" fallback, not a fabricated trend.
        "co_mg_m3_lag1": prev_rolling.get("co_mg_m3_1h", co),
        "pm2_5_ug_m3_lag1": prev_rolling.get("pm2_5_ug_m3_24h", pm2_5),
        "pm10_ug_m3_lag1": prev_rolling.get("pm10_ug_m3_24h", pm10),
    }


def _placeholder_classification(latest_reading: Optional[dict]) -> dict:
    if not latest_reading:
        return {"category": "Good", "dominant_pollutant": None, "confidence": None}
    return {
        "category": latest_reading.get("category", "Good"),
        "dominant_pollutant": latest_reading.get("dominant_pollutant"),
        "confidence": None,
    }


def _placeholder_forecast(latest_reading: Optional[dict], horizon_hours: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    if latest_reading:
        rolling = latest_reading.get("rolling_averages", {})
        pollutant_forecasts = {
            "co_mg_m3": rolling.get("co_mg_m3_1h"),
            "pm2_5_ug_m3": rolling.get("pm2_5_ug_m3_24h"),
            "pm10_ug_m3": rolling.get("pm10_ug_m3_24h"),
        }
        category = latest_reading.get("category", "Good")
        dominant_pollutant = latest_reading.get("dominant_pollutant")
    else:
        pollutant_forecasts = {"co_mg_m3": None, "pm2_5_ug_m3": None, "pm10_ug_m3": None}
        category = "Good"
        dominant_pollutant = None

    pollutant_ratios = _ratios(
        pollutant_forecasts["co_mg_m3"], pollutant_forecasts["pm2_5_ug_m3"], pollutant_forecasts["pm10_ug_m3"]
    )

    return [
        {
            "target_time": now + timedelta(hours=step * FORECAST_STEP_HOURS),
            "offset_hours": step * FORECAST_STEP_HOURS,
            "predicted_category": category,
            "predicted_dominant_pollutant": dominant_pollutant,
            "pollutant_forecasts": pollutant_forecasts,
            "pollutant_ratios": pollutant_ratios,
        }
        for step in range(1, max(1, horizon_hours) + 1)
    ]


def predict_classification_now(latest_reading: Optional[dict], previous_reading: Optional[dict] = None) -> dict:
    features = _extract_features(latest_reading, previous_reading)
    if MODEL_STATUS != "trained" or features is None:
        return _placeholder_classification(latest_reading)

    model = _classifier["model"]
    columns = _classifier["feature_columns"]
    row = [[features[c] for c in columns]]
    prediction = model.predict(row)[0]
    confidence = float(max(model.predict_proba(row)[0]))

    # dominant_pollutant isn't a model output (the classifier only predicts category) - derived
    # deterministically from the same current-reading values, same as the rule engine would.
    _, dominant = _category_and_dominant(features["co_1h"], features["pm2_5_24h"], features["pm10_24h"])

    return {"category": prediction, "dominant_pollutant": dominant, "confidence": confidence}


def predict_forecast(
    latest_reading: Optional[dict], previous_reading: Optional[dict], horizon_hours: int
) -> list[dict]:
    features = _extract_features(latest_reading, previous_reading)
    if MODEL_STATUS != "trained" or features is None:
        return _placeholder_forecast(latest_reading, horizon_hours)

    now = datetime.now(timezone.utc)
    state = dict(features)
    predictions = []

    for step in range(1, horizon_hours + 1):
        next_values = {}
        for pollutant, out_key in (("co", "co_mg_m3"), ("pm2_5", "pm2_5_ug_m3"), ("pm10", "pm10_ug_m3")):
            bundle = _forecasters[pollutant]
            row = [[state[c] for c in bundle["feature_columns"]]]
            next_values[out_key] = float(bundle["model"].predict(row)[0])

        category, dominant = _category_and_dominant(
            next_values["co_mg_m3"], next_values["pm2_5_ug_m3"], next_values["pm10_ug_m3"]
        )
        predictions.append(
            {
                "target_time": now + timedelta(hours=step * FORECAST_STEP_HOURS),
                "offset_hours": step * FORECAST_STEP_HOURS,
                "predicted_category": category,
                "predicted_dominant_pollutant": dominant,
                "pollutant_forecasts": next_values,
                "pollutant_ratios": _ratios(
                    next_values["co_mg_m3"], next_values["pm2_5_ug_m3"], next_values["pm10_ug_m3"]
                ),
            }
        )

        # Recursive single-step rollout: this step's prediction becomes next step's "current"
        # reading and new lag1 (hour_of_day/month held fixed - a documented simplification,
        # acceptable at the short horizons this project forecasts).
        state = {
            **state,
            "co_1h": next_values["co_mg_m3"],
            "pm2_5_24h": next_values["pm2_5_ug_m3"],
            "pm10_24h": next_values["pm10_ug_m3"],
            "co_mg_m3_lag1": state["co_1h"],
            "pm2_5_ug_m3_lag1": state["pm2_5_24h"],
            "pm10_ug_m3_lag1": state["pm10_24h"],
        }

    return predictions
