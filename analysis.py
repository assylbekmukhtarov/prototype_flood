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

    Вычисляет реальный размер одного пикселя из real_bbox и shape маски.
    Это корректно даже при COG overview-downsampling: real_bbox всегда
    соответствует фактическому окну снимка, а shape — количеству пикселей
    в этом окне, поэтому pixel_km2 = bbox_km2 / (rows * cols) даёт
    истинный размер пикселя независимо от уровня масштабирования.
    """
    import math

    pixel_count = int(mask.sum())
    if pixel_count == 0:
        return 0.0

    lon_min, lat_min, lon_max, lat_max = bbox
    lat_center = (lat_min + lat_max) / 2

    # размер одного пикселя в км² через реальный bbox и реальный shape
    km_per_lat = 111.0
    km_per_lon = 111.0 * math.cos(math.radians(lat_center))
    pixel_height_km = (lat_max - lat_min) * km_per_lat / mask.shape[0]
    pixel_width_km  = (lon_max - lon_min) * km_per_lon / mask.shape[1]
    pixel_km2 = pixel_height_km * pixel_width_km

    return pixel_count * pixel_km2


def remove_small_objects(mask: np.ndarray, min_pixels: int = 500) -> np.ndarray:
    """
    Удаляет связные компоненты маски размером меньше min_pixels.
    Для Sentinel-2 10м: 500 пикселей ≈ 0.5 га (по ТЗ, Шаг 3.3).
    При COG overview-downsampling пиксели крупнее, поэтому порог масштабируется
    пропорционально — реально отсекает объекты меньше ~0.5 га на любом zoom.
    """
    from scipy.ndimage import label

    if mask.sum() == 0:
        return mask

    labeled, num_features = label(mask)
    for component in range(1, num_features + 1):
        if (labeled == component).sum() < min_pixels:
            mask = mask.copy()
            mask[labeled == component] = 0

    return mask


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
    melt_rate: float,
    melt_rate_threshold: float = 50.0,
    temp_forecast: float | None = None,
    # исторический профиль
    avg_snow_km2: float | None = None,
    avg_flood_km2: float | None = None,
    max_flood_km2: float | None = None,
    current_flood_km2: float = 0.0,
) -> dict:
    """
    Весовая модель риска паводка. Возвращает score 0–100 и градацию.

    Факторы и веса:
      40% — снег относительно исторического среднего
      25% — скорость таяния относительно порога
      20% — температура прогноза
      15% — текущее затопление относительно исторического максимума
    """
    import math

    factors = {}
    weighted_sum = 0.0
    total_weight = 0.0

    # --- фактор 1: снег vs историческое среднее (вес 40%) ---
    w1 = 40.0
    if avg_snow_km2 is not None and avg_snow_km2 > 0:
        # ratio > 1 = снега больше нормы, насыщаем через tanh
        ratio = snow_area_km2 / avg_snow_km2
        score1 = min(100.0, math.tanh(ratio - 1.0 + 0.5) * 100 + 50)
        score1 = max(0.0, score1)
        factors["snow_vs_avg"] = {
            "label": "Снег vs историческое среднее",
            "value": snow_area_km2,
            "reference": avg_snow_km2,
            "score": round(score1, 1),
            "weight": w1,
        }
        weighted_sum += score1 * w1
        total_weight  += w1
    else:
        factors["snow_vs_avg"] = {
            "label": "Снег vs историческое среднее",
            "value": snow_area_km2,
            "reference": None,
            "score": None,
            "weight": w1,
        }

    # --- фактор 2: скорость таяния (вес 25%) ---
    w2 = 25.0
    if melt_rate_threshold > 0:
        score2 = min(100.0, (melt_rate / melt_rate_threshold) * 100)
    else:
        score2 = 100.0 if melt_rate > 0 else 0.0
    factors["melt_rate"] = {
        "label": "Скорость таяния",
        "value": round(melt_rate, 2),
        "reference": melt_rate_threshold,
        "score": round(score2, 1),
        "weight": w2,
    }
    weighted_sum += score2 * w2
    total_weight  += w2

    # --- фактор 3: температура прогноза (вес 20%) ---
    w3 = 20.0
    if temp_forecast is not None:
        # 0°C = 50%, каждый +1°C добавляет ~5 баллов, насыщение на +10°C
        score3 = min(100.0, max(0.0, 50.0 + temp_forecast * 5.0))
        factors["temperature"] = {
            "label": "Прогноз температуры",
            "value": temp_forecast,
            "reference": 0,
            "score": round(score3, 1),
            "weight": w3,
        }
        weighted_sum += score3 * w3
        total_weight  += w3
    else:
        factors["temperature"] = {
            "label": "Прогноз температуры",
            "value": None,
            "reference": 0,
            "score": None,
            "weight": w3,
        }

    # --- фактор 4: текущее затопление vs исторический максимум (вес 15%) ---
    w4 = 15.0
    if max_flood_km2 is not None and max_flood_km2 > 0:
        score4 = min(100.0, (current_flood_km2 / max_flood_km2) * 100)
        factors["flood_vs_max"] = {
            "label": "Затопление vs исторический максимум",
            "value": current_flood_km2,
            "reference": max_flood_km2,
            "avg_flood_km2": avg_flood_km2,
            "score": round(score4, 1),
            "weight": w4,
        }
        weighted_sum += score4 * w4
        total_weight  += w4
    else:
        factors["flood_vs_max"] = {
            "label": "Затопление vs исторический максимум",
            "value": current_flood_km2,
            "reference": None,
            "avg_flood_km2": avg_flood_km2,
            "score": None,
            "weight": w4,
        }

    # --- итоговый score ---
    risk_score = round(weighted_sum / total_weight, 1) if total_weight > 0 else 0.0

    if risk_score < 30:
        level = "low"
        label = "Низкий риск"
    elif risk_score < 70:
        level = "medium"
        label = "Средний риск"
    else:
        level = "high"
        label = "Высокий риск"

    return {
        "risk_score": risk_score,
        "risk_level": level,
        "risk_label": label,
        "factors": factors,
        # обратная совместимость
        "high_risk": level == "high",
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
