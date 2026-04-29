import io
import csv
import traceback
import urllib.request
import json as json_lib
from datetime import datetime, timedelta, date
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel
import numpy as np

from sentinel import load_bands, compute_historical_profile, load_rgb
from analysis import (
    compute_indices,
    compute_masks,
    compute_area_km2,
    compute_new_flood,
    compute_melt_rate,
    assess_risk,
    mask_to_geojson,
    remove_small_objects,
)

app = FastAPI(title="Flood Monitor")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "traceback": traceback.format_exc()},
    )


@app.get("/")
def index():
    return FileResponse("static/index.html")


class AnalyzeRequest(BaseModel):
    bbox: list[float]           # [minx, miny, maxx, maxy]
    date_snow_start: str        # YYYY-MM-DD
    date_snow_end: str
    date_before: str
    date_after: str
    snow_threshold: float = 0.4
    water_threshold: float = 0.0
    melt_rate_threshold: float = 50.0
    temp_forecast: float | None = None
    historical_years: int = 0


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    """
    Основной эндпоинт: загружает три снимка (снег/до/после),
    вычисляет NDSI/MNDWI, маски, площади, оценку риска.
    """
    if req.date_before == req.date_after:
        raise HTTPException(
            status_code=400,
            detail="date_before и date_after совпадают — невозможно вычислить новое затопление"
        )

    # --- снимок за снежный период ---
    snow_data = load_bands(req.bbox, req.date_snow_start, req.date_snow_end)
    if snow_data is None:
        raise HTTPException(
            status_code=404,
            detail="Нет снимков Sentinel-2 за снежный период с облачностью < 20%"
        )

    ndsi, _ = compute_indices(snow_data["b3"], snow_data["b11"])
    snow_mask, _ = compute_masks(ndsi, ndsi, req.snow_threshold, req.water_threshold)
    snow_mask = remove_small_objects(snow_mask)
    snow_area = compute_area_km2(snow_mask, snow_data["real_bbox"])

    # --- снимок "до паводка" ---
    before_data = load_bands(req.bbox, req.date_before, req.date_before)
    if before_data is None:
        d = datetime.strptime(req.date_before, "%Y-%m-%d")
        before_data = load_bands(
            req.bbox,
            (d - timedelta(days=7)).strftime("%Y-%m-%d"),
            (d + timedelta(days=7)).strftime("%Y-%m-%d"),
        )
    if before_data is None:
        raise HTTPException(status_code=404, detail=f"Нет снимков около даты {req.date_before}")

    _, mndwi_before = compute_indices(before_data["b3"], before_data["b11"])
    _, water_before = compute_masks(mndwi_before, mndwi_before, req.snow_threshold, req.water_threshold)
    water_before = remove_small_objects(water_before)
    water_area_before = compute_area_km2(water_before, before_data["real_bbox"])

    # --- снимок "после паводка" ---
    after_data = load_bands(req.bbox, req.date_after, req.date_after)
    if after_data is None:
        d = datetime.strptime(req.date_after, "%Y-%m-%d")
        after_data = load_bands(
            req.bbox,
            (d - timedelta(days=7)).strftime("%Y-%m-%d"),
            (d + timedelta(days=7)).strftime("%Y-%m-%d"),
        )
    if after_data is None:
        raise HTTPException(status_code=404, detail=f"Нет снимков около даты {req.date_after}")

    _, mndwi_after = compute_indices(after_data["b3"], after_data["b11"])
    _, water_after = compute_masks(mndwi_after, mndwi_after, req.snow_threshold, req.water_threshold)
    water_after = remove_small_objects(water_after)
    water_area_after = compute_area_km2(water_after, after_data["real_bbox"])

    # --- новое затопление ---
    # water_area_before уже посчитан выше по before_data["real_bbox"] — это верно.
    # Для compute_new_flood нужно привести water_before к shape water_after,
    # после чего оба массива считаются относительно after_data["real_bbox"].
    if water_before.shape != water_after.shape:
        from scipy.ndimage import zoom as _zoom
        zy = water_after.shape[0] / water_before.shape[0]
        zx = water_after.shape[1] / water_before.shape[1]
        water_before = (_zoom(water_before.astype(float), (zy, zx), order=0) > 0.5).astype(np.uint8)

    flood_mask = compute_new_flood(water_before, water_after)
    flood_mask = remove_small_objects(flood_mask)
    new_flood_area = compute_area_km2(flood_mask, after_data["real_bbox"])

    # --- снимок за период "после" для расчёта таяния ---
    snow_after_data = load_bands(req.bbox, req.date_after, req.date_after)
    if snow_after_data is None:
        d = datetime.strptime(req.date_after, "%Y-%m-%d")
        snow_after_data = load_bands(
            req.bbox,
            (d - timedelta(days=7)).strftime("%Y-%m-%d"),
            (d + timedelta(days=7)).strftime("%Y-%m-%d"),
        )
    if snow_after_data is not None:
        ndsi_after, _ = compute_indices(snow_after_data["b3"], snow_after_data["b11"])
        snow_mask_after, _ = compute_masks(ndsi_after, ndsi_after, req.snow_threshold, req.water_threshold)
        snow_mask_after = remove_small_objects(snow_mask_after)
        snow_area_after = compute_area_km2(snow_mask_after, snow_after_data["real_bbox"])
    else:
        snow_area_after = 0.0

    # --- вычисляет скорость таяния ---
    try:
        d_snow = datetime.strptime(req.date_snow_end, "%Y-%m-%d")
        d_after = datetime.strptime(req.date_after, "%Y-%m-%d")
        days = max(1, (d_after - d_snow).days)
    except Exception:
        days = 30
    melt_rate = compute_melt_rate(snow_area, snow_area_after, days)

    # --- исторический профиль (если задан historical_years > 0) ---
    historical_profile = None
    if req.historical_years > 0:
        historical_profile = compute_historical_profile(
            bbox=req.bbox,
            date_snow_start=req.date_snow_start,
            date_snow_end=req.date_snow_end,
            date_before=req.date_before,
            date_after=req.date_after,
            historical_years=req.historical_years,
            snow_threshold=req.snow_threshold,
            water_threshold=req.water_threshold,
        )

    # --- оценивает риск ---
    hp = historical_profile or {}
    risk = assess_risk(
        snow_area_km2=snow_area,
        melt_rate=melt_rate,
        melt_rate_threshold=req.melt_rate_threshold,
        temp_forecast=req.temp_forecast,
        avg_snow_km2=hp.get("avg_snow_km2"),
        avg_flood_km2=hp.get("avg_flood_km2"),
        max_flood_km2=hp.get("max_flood_km2"),
        current_flood_km2=new_flood_area,
    )

    # --- формирует GeoJSON масок (использует real_bbox каждого снимка) ---
    snow_geojson  = mask_to_geojson(snow_mask,  snow_data["real_bbox"])
    water_geojson = mask_to_geojson(water_after, after_data["real_bbox"])
    flood_geojson = mask_to_geojson(flood_mask,  after_data["real_bbox"])

    return {
        "metrics": {
            "snow_area_km2": round(snow_area, 2),
            "water_area_before_km2": round(water_area_before, 2),
            "water_area_after_km2": round(water_area_after, 2),
            "new_flood_area_km2": round(new_flood_area, 2),
            "melt_rate_km2_per_day": round(melt_rate, 2),
        },
        "risk": risk,
        "historical": historical_profile,
        "scenes": {
            "snow": {"date": snow_data["date"], "cloud_cover": snow_data["cloud_cover"]},
            "before": {"date": before_data["date"], "cloud_cover": before_data["cloud_cover"]},
            "after": {"date": after_data["date"], "cloud_cover": after_data["cloud_cover"]},
        },
        "layers": {
            "snow": snow_geojson,
            "water": water_geojson,
            "flood": flood_geojson,
        },
    }


@app.get("/api/temperature")
def get_temperature(lat: float, lon: float, date_start: str, date_end: str):
    """
    Получает среднюю температуру за период из Open-Meteo.
    Автоматически выбирает источник: исторические данные (ERA5) или прогноз — по датам.
    date_start / date_end: YYYY-MM-DD
    """
    today = date.today()
    d_start = datetime.strptime(date_start, "%Y-%m-%d").date()
    d_end   = datetime.strptime(date_end,   "%Y-%m-%d").date()

    # Open-Meteo historical archive доступен с задержкой 5 дней
    archive_cutoff = today - timedelta(days=5)

    if d_end <= archive_cutoff:
        # весь период в прошлом — берёт архив ERA5
        url = (
            f"https://archive-api.open-meteo.com/v1/archive"
            f"?latitude={lat}&longitude={lon}"
            f"&start_date={date_start}&end_date={date_end}"
            f"&daily=temperature_2m_max,temperature_2m_min"
            f"&timezone=auto"
        )
        source = "historical"
    elif d_start >= today:
        # весь период в будущем — берёт прогноз
        # Open-Meteo даёт прогноз до 16 дней
        forecast_end = min(d_end, today + timedelta(days=15))
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&start_date={date_start}&end_date={forecast_end.strftime('%Y-%m-%d')}"
            f"&daily=temperature_2m_max,temperature_2m_min"
            f"&timezone=auto"
        )
        source = "forecast"
    else:
        # период частично в прошлом, частично в будущем — берёт архив до сегодня
        actual_end = min(d_end, archive_cutoff)
        url = (
            f"https://archive-api.open-meteo.com/v1/archive"
            f"?latitude={lat}&longitude={lon}"
            f"&start_date={date_start}&end_date={actual_end.strftime('%Y-%m-%d')}"
            f"&daily=temperature_2m_max,temperature_2m_min"
            f"&timezone=auto"
        )
        source = "historical+partial"

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json_lib.loads(resp.read())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Open-Meteo недоступен: {e}")

    daily = data.get("daily", {})
    t_max = daily.get("temperature_2m_max", [])
    t_min = daily.get("temperature_2m_min", [])
    times = daily.get("time", [])

    if not t_max:
        raise HTTPException(status_code=404, detail="Open-Meteo не вернул данные за этот период")

    # вычисляет среднее из (max+min)/2 по всем дням периода
    avg_temps = [
        (mx + mn) / 2
        for mx, mn in zip(t_max, t_min)
        if mx is not None and mn is not None
    ]
    avg = round(sum(avg_temps) / len(avg_temps), 1) if avg_temps else None

    return {
        "lat": lat,
        "lon": lon,
        "date_start": date_start,
        "date_end": date_end,
        "source": source,
        "avg_temp_c": avg,
        "days_count": len(avg_temps),
        "daily": [
            {"date": t, "max": mx, "min": mn}
            for t, mx, mn in zip(times, t_max, t_min)
        ],
    }


@app.post("/api/export-csv")
def export_csv(req: AnalyzeRequest):
    """Возвращает CSV с метриками, вызывает логику /api/analyze напрямую."""
    result = analyze(req)
    m = result["metrics"]
    r = result["risk"]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["date_after", "snow_area_km2", "water_area_km2",
                     "new_flood_area_km2", "melt_rate_km2_per_day",
                     "risk_score", "risk_level"])
    writer.writerow([
        req.date_after,
        m["snow_area_km2"],
        m["water_area_after_km2"],
        m["new_flood_area_km2"],
        m["melt_rate_km2_per_day"],
        r["risk_score"],
        r["risk_level"],
    ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=flood_report.csv"},
    )


@app.get("/api/rgb")
def get_rgb(
    bbox: str,           # "minx,miny,maxx,maxy"
    date_start: str,
    date_end: str,
):
    """
    Возвращает PNG с RGB-композитом (B4/B3/B2) для заданного bbox и периода.
    Используется фронтом как ImageOverlay поверх карты Leaflet.
    """
    import io as _io
    try:
        from PIL import Image
    except ImportError:
        raise HTTPException(status_code=500, detail="Pillow не установлен: pip install Pillow")

    try:
        minx, miny, maxx, maxy = [float(v) for v in bbox.split(",")]
    except ValueError:
        raise HTTPException(status_code=400, detail="bbox должен быть 'minx,miny,maxx,maxy'")

    data = load_rgb([minx, miny, maxx, maxy], date_start, date_end)
    if data is None:
        raise HTTPException(status_code=404, detail="Нет снимков за указанный период")

    def norm_band(arr: np.ndarray) -> np.ndarray:
        # обрезаем яркие выбросы (облака, снег) через 2–98 перцентиль
        p2, p98 = np.percentile(arr, 2), np.percentile(arr, 98)
        stretched = np.clip((arr - p2) / (p98 - p2 + 1e-6), 0, 1)
        return (stretched * 255).astype(np.uint8)

    r = norm_band(data["b4"])
    g = norm_band(data["b3"])
    b = norm_band(data["b2"])

    rgb = np.stack([r, g, b], axis=-1)
    img = Image.fromarray(rgb, mode="RGB")

    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="image/png",
        headers={"real_bbox": ",".join(str(round(v, 6)) for v in data["real_bbox"]),
                 "scene_date": data["date"]},
    )
