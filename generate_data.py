"""
AXON demo — synthetic telemetry + condition-monitoring analysis for Pump P-101.

Data-science approach (mirrors §10 of the design doc):
  1. Simulate 60 days of hourly sensor data for a centrifugal pump:
     vibration RMS (mm/s, ISO 10816 zones), bearing temperature (°C),
     motor current (A). A bearing-wear fault initiates ~day 38 and grows
     exponentially (Paris-law-like crack growth), on top of daily thermal
     cycles and process noise.
  2. Detect the anomaly with an EWMA control chart (z-score vs a healthy
     baseline window) — the same family of explainable techniques the doc
     calls for (autoencoder / isolation forest class; EWMA keeps the demo
     transparent and reproducible).
  3. Estimate the changepoint, fit an exponential degradation model to the
     post-changepoint envelope, and extrapolate to the ISO 10816 Zone D
     trip threshold (7.1 mm/s) → Remaining Useful Life with a bootstrap
     confidence interval.
  4. Emit everything as JSON for the front-end (downsampled to 4 h).
"""

import json
import numpy as np

rng = np.random.default_rng(42)

HOURS = 60 * 24                       # 60 days, hourly
t = np.arange(HOURS)
days = t / 24.0

# ---- 1. Simulate -----------------------------------------------------------
# Healthy vibration baseline ~2.3 mm/s (ISO Zone B for a class II machine)
base_vib = 2.3
daily_cycle = 0.12 * np.sin(2 * np.pi * t / 24 + 0.6)           # thermal/process cycle
noise = rng.normal(0, 0.09, HOURS)

# Bearing fault initiates day 38, exponential growth (outer-race defect)
t0 = 38 * 24
growth = np.where(t > t0, 0.55 * (np.exp((t - t0) / (17.5 * 24)) - 1), 0.0)
# Sporadic impact spikes once the fault is active
spikes = np.where(
    (t > t0) & (rng.random(HOURS) < 0.02),
    rng.exponential(0.5, HOURS),
    0.0,
)
vib = base_vib + daily_cycle + noise + growth + spikes

# Bearing temperature: 58 °C baseline, tracks fault with lag + ambient cycle
temp = (
    58
    + 2.0 * np.sin(2 * np.pi * t / 24 - 1.2)
    + rng.normal(0, 0.5, HOURS)
    + np.where(t > t0 + 48, 6.5 * (1 - np.exp(-(t - t0 - 48) / (9 * 24))), 0)
)

# Motor current: 41 A baseline, mild rise with load/friction
amps = (
    41
    + 0.8 * np.sin(2 * np.pi * t / 24 + 2.0)
    + rng.normal(0, 0.35, HOURS)
    + np.where(t > t0, 1.6 * (1 - np.exp(-(t - t0) / (14 * 24))), 0)
)

# ---- 2. Detect (EWMA control chart on vibration) ---------------------------
baseline = vib[: 30 * 24]                       # first 30 days = healthy reference
mu, sigma = baseline.mean(), baseline.std()

lam = 0.08                                       # EWMA smoothing
ewma = np.zeros(HOURS)
ewma[0] = vib[0]
for i in range(1, HOURS):
    ewma[i] = lam * vib[i] + (1 - lam) * ewma[i - 1]

sigma_ewma = sigma * np.sqrt(lam / (2 - lam))
z = (ewma - mu) / sigma_ewma
ucl = 3.0                                        # 3-sigma control limit
anomaly_mask = z > ucl
anomaly_mask[: 30 * 24] = False                  # burn-in: baseline window can't alarm

# First *sustained* excursion: 12 consecutive hours above the UCL
run = np.convolve(anomaly_mask.astype(int), np.ones(12, dtype=int), mode="valid")
sustained = np.where(run == 12)[0]
first_alarm = int(sustained[0]) if len(sustained) else HOURS - 1

# ---- 3. Prognose (exponential fit → RUL) -----------------------------------
# Changepoint: last time EWMA was inside control limits before the alarm run
cp = first_alarm
fit_t = t[cp:]
fit_y = ewma[cp:] - mu
fit_y = np.clip(fit_y, 1e-3, None)
# ln(y) = ln(a) + b·t  → linear fit
A = np.vstack([np.ones_like(fit_t, dtype=float), fit_t.astype(float)]).T
coef, *_ = np.linalg.lstsq(A, np.log(fit_y), rcond=None)
ln_a, b = coef

THRESHOLD = 7.1                                  # ISO 10816 Zone C/D boundary, mm/s
t_fail = (np.log(THRESHOLD - mu) - ln_a) / b     # hours from t=0
now = HOURS - 1
rul_hours = t_fail - now

# Bootstrap CI on RUL (resample fit residuals)
resid = np.log(fit_y) - (ln_a + b * fit_t)
boots = []
for _ in range(500):
    yb = ln_a + b * fit_t + rng.choice(resid, size=len(fit_t), replace=True)
    cb, *_ = np.linalg.lstsq(A, yb, rcond=None)
    tf = (np.log(THRESHOLD - mu) - cb[0]) / cb[1]
    boots.append(tf - now)
boots = np.array(boots)
rul_lo, rul_hi = np.percentile(boots, [5, 95])

# Forecast curve for the chart: extend 14 days past "now"
f_t = np.arange(now, now + 14 * 24, 4)
f_mean = mu + np.exp(ln_a + b * f_t)
f_lo = mu + np.exp(ln_a + b * f_t + np.percentile(resid, 5))
f_hi = mu + np.exp(ln_a + b * f_t + np.percentile(resid, 95))

# Feature attribution for the fault call (envelope-band energies, plausible
# values for an outer-race bearing defect signature — BPFO dominant)
features = [
    {"name": "BPFO band energy (107 Hz)", "value": 0.86},
    {"name": "RMS velocity trend slope", "value": 0.71},
    {"name": "Crest factor", "value": 0.54},
    {"name": "Bearing temp residual", "value": 0.47},
    {"name": "BPFI band energy (142 Hz)", "value": 0.18},
    {"name": "Motor current THD", "value": 0.12},
]

# ---- 4. Emit ---------------------------------------------------------------
DS = 4                                           # downsample to 4-hourly
sl = slice(0, HOURS, DS)

out = {
    "meta": {
        "asset": "P-101",
        "sensor": "VT-101 (DE bearing, radial)",
        "unit": "mm/s RMS",
        "days": 60,
        "step_hours": DS,
        "baseline_mean": round(float(mu), 3),
        "baseline_sigma": round(float(sigma), 3),
        "threshold": THRESHOLD,
        "alarm_zone": 4.5,                        # ISO Zone B/C boundary
        "changepoint_day": round(cp / 24, 1),
        "first_alarm_day": round(first_alarm / 24, 1),
        "now_day": round(now / 24, 1),
        "rul_days": round(float(rul_hours) / 24, 1),
        "rul_ci_days": [round(float(rul_lo) / 24, 1), round(float(rul_hi) / 24, 1)],
        "model": "EWMA control chart (λ=0.08, 3σ) + exponential degradation fit, 500-sample bootstrap CI",
    },
    "vib": [round(float(v), 3) for v in vib[sl]],
    "ewma": [round(float(v), 3) for v in ewma[sl]],
    "temp": [round(float(v), 2) for v in temp[sl]],
    "amps": [round(float(v), 2) for v in amps[sl]],
    "anomaly": [bool(a) for a in anomaly_mask[sl]],
    "forecast": {
        "t_days": [round(float(x) / 24, 2) for x in f_t],
        "mean": [round(float(v), 3) for v in f_mean],
        "lo": [round(float(v), 3) for v in f_lo],
        "hi": [round(float(v), 3) for v in f_hi],
    },
    "features": features,
}

with open("telemetry.json", "w") as f:
    json.dump(out, f)

m = out["meta"]
print(f"changepoint day {m['changepoint_day']}, first alarm day {m['first_alarm_day']}")
print(f"RUL = {m['rul_days']} days  (90% CI {m['rul_ci_days'][0]}–{m['rul_ci_days'][1]})")
print(f"points per series: {len(out['vib'])}")
