import numpy as np
from shapely.geometry import shape, mapping
from shapely.ops import unary_union
import rasterio
import rasterio.features
from rasterio.transform import from_bounds


def compute_indices(b3: np.ndarray, b11: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """NDSI и MNDWI по формуле (B3 - B11) / (B3 + B11)."""
    denom = b3 + b11
    # избегает деления на ноль
    valid = denom != 0
    index = np.where(valid, (b3 - b11) / np.where(valid, denom, 1), 0.0)
    return index, index  # ndsi и mndwi одна формула, разная интерпретация


def compute_masks(
    ndsi: np.ndarray,
    mndwi: np.ndarray,
    snow_threshold: float = 0.4,
    water_threshold: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    snow_mask = (ndsi > snow_threshold).astype(np.uint8)
    water_mask = (mndwi > water_threshold).astype(np.uint8)
    return snow_mask, water_mask


def compute_area_km2(mask: np.ndarray, bbox: list[float]) -> float:
    """
    Считает площадь маски в км².
    Пиксель = (bbox_area / total_pixels) в градусах → конвертирует через приближение.
    """
    pixel_count = int(mask.sum())
    if pixel_count == 0:
        return 0.0

    # площадь bbox в градусах → в км² (грубое приближение)
    lon_min, lat_min, lon_max, lat_max = bbox
    lat_center = (lat_min + lat_max) / 2

    # 1 градус широты = 111 км, 1 градус долготы = 111*cos(lat) км
    import math
    km_per_lat = 111.0
    km_per_lon = 111.0 * math.cos(math.radians(lat_center))

    bbox_km2 = (lon_max - lon_min) * km_per_lon * (lat_max - lat_min) * km_per_lat
    total_pixels = mask.shape[0] * mask.shape[1]

    return pixel_count * bbox_km2 / total_pixels


def compute_new_flood(
    water_before: np.ndarray, water_after: np.ndarray
) -> np.ndarray:
    """Новое затопление: вода есть после, но не было до."""
    return ((water_after == 1) & (water_before == 0)).astype(np.uint8)


def compute_melt_rate(
    snow_area_before_km2: float,
    snow_area_after_km2: float,
    days: int,
) -> float:
    """Скорость таяния в км²/день."""
    if days <= 0:
        return 0.0
    return max(0.0, (snow_area_before_km2 - snow_area_after_km2) / days)


def assess_risk(
    snow_area_km2: float,
    historical_avg_km2: float | None,
    melt_rate: float,
    melt_rate_threshold: float = 50.0,
    temp_forecast: float | None = None,
) -> dict:
    """
    Оценивает риск паводка по трём условиям.
    Возвращает dict с результатом и объяснением.
    """
    conditions = {}
    triggered = []

    # условие 1: снег выше исторического среднего
    if historical_avg_km2 is not None and historical_avg_km2 > 0:
        cond1 = snow_area_km2 > historical_avg_km2
        conditions["snow_above_avg"] = {
            "triggered": cond1,
            "value": snow_area_km2,
            "threshold": historical_avg_km2,
            "label": "Снег выше исторического среднего",
        }
        if cond1:
            triggered.append("snow_above_avg")
    else:
        conditions["snow_above_avg"] = {"triggered": None, "label": "Нет исторических данных"}

    # условие 2: скорость таяния выше порога
    cond2 = melt_rate > melt_rate_threshold
    conditions["melt_rate"] = {
        "triggered": cond2,
        "value": round(melt_rate, 2),
        "threshold": melt_rate_threshold,
        "label": "Скорость таяния выше порога",
    }
    if cond2:
        triggered.append("melt_rate")

    # условие 3: температура > 0 (если задана)
    if temp_forecast is not None:
        cond3 = temp_forecast > 0
        conditions["temp_positive"] = {
            "triggered": cond3,
            "value": temp_forecast,
            "threshold": 0,
            "label": "Температура выше 0°C",
        }
        if cond3:
            triggered.append("temp_positive")
        required = 3
    else:
        conditions["temp_positive"] = {"triggered": None, "label": "Температура не задана"}
        required = 2

    active = [k for k, v in conditions.items() if v.get("triggered") is True]
    high_risk = len(active) >= required

    return {
        "high_risk": high_risk,
        "triggered_count": len(active),
        "required_count": required,
        "conditions": conditions,
    }


def mask_to_geojson(mask: np.ndarray, bbox: list[float]) -> dict:
    """Конвертирует бинарную маску в GeoJSON полигоны."""
    if mask.sum() == 0:
        return {"type": "FeatureCollection", "features": []}

    lon_min, lat_min, lon_max, lat_max = bbox
    transform = from_bounds(lon_min, lat_min, lon_max, lat_max, mask.shape[1], mask.shape[0])

    shapes = list(rasterio.features.shapes(mask, transform=transform))
    features = []
    for geom, value in shapes:
        if value == 1:
            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {},
            })

    return {"type": "FeatureCollection", "features": features}
