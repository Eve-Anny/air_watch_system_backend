# backend/

FastAPI + MongoDB backend for the IoT Air Quality Management System. Implements the API surface,
data model, WHO rolling-window scoring engine, and alert engine from `ARCHITECTURE.md`.

## Layout

```
backend/
├── requirements.txt
├── .env.example
└── app/
    ├── main.py                # FastAPI app, CORS, router registration, Mongo lifespan
    ├── core/
    │   ├── config.py            # env-based settings (pydantic-settings)
    │   ├── constants.py          # WHO guideline values, rolling windows, band thresholds
    │   └── serialization.py       # Mongo ObjectId -> JSON-safe "id" field helpers
    ├── db/
    │   └── mongodb.py            # Motor client, index creation
    ├── models/                   # Pydantic v2 schemas (readings, devices, alerts)
    ├── services/
    │   ├── scoring_engine.py      # rolling averages, WHO ratios, category, dominant pollutant
    │   ├── alert_engine.py         # Info/Warning/Critical rules, de-dup, CO spike override
    │   ├── device_service.py        # auto-register-on-first-reading, computed online/offline status
    │   └── ml_inference.py           # Phase 5 forecast/classification placeholder + integration contract
    └── routers/                    # readings, devices, alerts, status, forecast, health
```

## Local setup

1. **MongoDB running locally** (e.g. `docker run -d -p 27017:27017 mongo:7` or a local install).
2. `cd backend && python -m venv .venv && source .venv/bin/activate` (or your preferred venv tool).
3. `pip install -r requirements.txt`
4. `cp .env.example .env` — defaults already point at `mongodb://localhost:27017`.
5. Run: `uvicorn app.main:app --reload --port 8000`
6. Interactive API docs: `http://localhost:8000/docs` (FastAPI's auto-generated OpenAPI UI).

## Environment variables

| Variable | Local dev default | Atlas / deployed value |
|---|---|---|
| `MONGODB_URI` | `mongodb://localhost:27017` | `mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority` |
| `MONGODB_DB_NAME` | `air_quality` | same, or a separate prod DB name |
| `ALLOWED_ORIGINS` | `http://localhost:5173` (Vite dev server) | your deployed frontend origin, e.g. `https://your-app.vercel.app` — comma-separate multiple |
| `DEVICE_OFFLINE_THRESHOLD_SECONDS` | `180` | tune to roughly 3-6x the firmware's sample interval |

For the defense deployment (Render/Railway per `ARCHITECTURE.md`'s deployment plan), set these same
variables in the platform's environment-variable settings — no code changes needed, per Phase 0's
"same codebase runs local or deployed" requirement.

## Ingestion contract

The firmware POSTs to `POST /api/v1/readings` with the shape produced by
`firmware/src/network/api_client.cpp`:

```json
{
  "device_id": "esp32-A1B2C3",
  "timestamp": "2026-07-31T12:00:00Z",
  "raw": {
    "co_mg_m3": 1.2, "voc_index": 143,
    "pm1_0_ug_m3": 4.0, "pm2_5_ug_m3": 9.5, "pm10_ug_m3": 14.2,
    "temperature_c": 27.4, "humidity_pct": 61.0, "pressure_hpa": 1008.3
  }
}
```

Any `raw` field can be `null` (sensor read failure) — the scoring engine treats missing data as "no
signal this cycle" rather than crashing or fabricating a value. Malformed payloads (wrong types,
missing `device_id`/`timestamp`) are rejected with FastAPI's standard `422 Unprocessable Entity` and
a field-level error body — never silently dropped.

## Forecast endpoint status

`GET /api/v1/forecast` is backed by real trained Random Forest models as of Phase 5 (classifier +
per-pollutant forecasters, trained in `ml/` and copied to `app/ml_models/*.joblib`). See
`app/services/ml_inference.py`'s module docstring and `ml/README.md` for the full rule-engine-vs-ML
relationship, dataset, and methodology writeup. If `app/ml_models/` is empty (e.g. a fresh clone
before `python ml/train.py` has been run), the endpoint falls back automatically to a naive
persistence placeholder — `model_status` in the response tells you which mode is active
(`"trained"` vs `"placeholder"`). The response shape itself never changes between the two.
