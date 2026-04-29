import numpy as np
import rasterio
from rasterio.windows import from_bounds
from rasterio.enums import Resampling
from pystac_client import Client
from pyproj import Transformer
from datetime import datetime, timedelta

STAC_URL = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"


def search_best_scene(bbox: list[float], date_start: str, date_end: str) -> dict | None:
    """Ищет снимок с минимальной облачностью за период.
    Отправляет запрос к STAC-каталогу Element84 (earth-search.aws.element84.com),
    получает список снимков Sentinel-2 за bbox и период с облачностью < 20%
    (только метаданные, не сами пиксели), сортирует по облачности, возвращает лучший.
    """
    client = Client.open(STAC_URL)
    search = client.search(
        collections=[COLLECTION],
        bbox=bbox,
        datetime=f"{date_start}/{date_end}",
        query={"eo:cloud_cover": {"lt": 20}},
        max_items=20,
    )
    items = list(search.items())
    if not items:
        return None
    items.sort(key=lambda i: i.properties.get("eo:cloud_cover", 100))
    return items[0]


def read_band_window(item, band_name: str, bbox: list[float]) -> tuple[np.ndarray, list[float]]:
    """
    Читает только нужный bbox из COG-файла через HTTP Range запрос.
    bbox = [minx, miny, maxx, maxy] в EPSG:4326
    Возвращает (массив, реальный_bbox_в_WGS84) — bbox может чуть отличаться
    из-за выравнивания по пикселям снимка.
    
    По сути мы берём URL файла из метаданных (это COG на AWS S3)
    Через HTTP Range Request скачиваем ТОЛЬКО пиксели внутри bbox
    (не весь тайл 300MB, а 2-5MB)
    """
    asset = item.assets.get(band_name)
    if asset is None:
        raise ValueError(f"Band {band_name} not found in item {item.id}")

    href = asset.href

    with rasterio.open(href) as src:
        # трансформирует bbox из WGS84 в CRS снимка
        to_src = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
        minx, miny = to_src.transform(bbox[0], bbox[1])
        maxx, maxy = to_src.transform(bbox[2], bbox[3])

        window = from_bounds(minx, miny, maxx, maxy, src.transform)

        # читает с overview для скорости если окно большое
        overview_level = _choose_overview(src, window)
        out_h = max(1, int(window.height / (2 ** overview_level)))
        out_w = max(1, int(window.width  / (2 ** overview_level)))

        data = src.read(
            1,
            window=window,
            out_shape=(out_h, out_w),
            resampling=Resampling.average,
        ).astype(np.float32)

        # вычисляет реальный bbox прочитанного окна (с учётом выравнивания)
        win_transform = rasterio.windows.transform(window, src.transform)
        real_minx = win_transform.c
        real_maxy = win_transform.f
        real_maxx = real_minx + win_transform.a * window.width
        real_miny = real_maxy + win_transform.e * window.height

        # переводит обратно в WGS84
        to_wgs = Transformer.from_crs(src.crs, "EPSG:4326", always_xy=True)
        lon_min, lat_min = to_wgs.transform(real_minx, real_miny)
        lon_max, lat_max = to_wgs.transform(real_maxx, real_maxy)
        real_bbox = [lon_min, lat_min, lon_max, lat_max]

    return data, real_bbox


def _choose_overview(src, window) -> int:
    """Выбирает уровень overview так, чтобы результат был не больше 512x512 пикселей."""
    h = window.height
    w = window.width
    level = 0
    while (h / (2 ** level) > 512 or w / (2 ** level) > 512) and level < 4:
        level += 1
    return level


def load_bands(bbox: list[float], date_start: str, date_end: str) -> dict | None:
    """
    Загружает B3 и B11 для расчёта NDSI/MNDWI.
    Возвращает dict с массивами и метаданными, или None если снимков нет.
    """
    item = search_best_scene(bbox, date_start, date_end)
    if item is None:
        return None

    b3,  real_bbox = read_band_window(item, "green",  bbox)   # B3
    b11, _         = read_band_window(item, "swir16", bbox)   # B11

    # приводит к одному размеру (B11 может быть 20м vs B3 10м)
    if b3.shape != b11.shape:
        b11 = _resize(b11, b3.shape)

    # деление на 10000 не требуется — NDSI/MNDWI инвариантны к масштабу

    return {
        "b3": b3,
        "b11": b11,
        "real_bbox": real_bbox,
        "date": item.datetime.strftime("%Y-%m-%d") if item.datetime else date_start,
        "cloud_cover": item.properties.get("eo:cloud_cover", None),
        "item_id": item.id,
    }


def load_rgb(bbox: list[float], date_start: str, date_end: str) -> dict | None:
    """
    Загружает B4 (red), B3 (green), B2 (blue) для RGB-визуализации.
    Возвращает dict с тремя массивами, real_bbox и датой, или None если снимков нет.
    """
    item = search_best_scene(bbox, date_start, date_end)
    if item is None:
        return None

    b4, real_bbox = read_band_window(item, "red",   bbox)   # B4
    b3, _         = read_band_window(item, "green", bbox)   # B3
    b2, _         = read_band_window(item, "blue",  bbox)   # B2

    # приводит все к одному размеру по B4
    target = b4.shape
    if b3.shape != target:
        b3 = _resize(b3, target)
    if b2.shape != target:
        b2 = _resize(b2, target)

    return {
        "b4": b4,
        "b3": b3,
        "b2": b2,
        "real_bbox": real_bbox,
        "date": item.datetime.strftime("%Y-%m-%d") if item.datetime else date_start,
    }


def _resize(arr: np.ndarray, target_shape: tuple) -> np.ndarray:
    """Изменяет размер массива до target_shape через scipy.ndimage.zoom."""
    zoom_y = target_shape[0] / arr.shape[0]
    zoom_x = target_shape[1] / arr.shape[1]
    from scipy.ndimage import zoom
    return zoom(arr, (zoom_y, zoom_x), order=1).astype(np.float32)


def compute_historical_profile(
    bbox: list[float],
    date_snow_start: str,
    date_snow_end: str,
    date_before: str,
    date_after: str,
    historical_years: int,
    snow_threshold: float = 0.4,
    water_threshold: float = 0.0,
) -> dict:
    """
    За каждый из N предыдущих лет загружает снимки снежного периода,
    «до» и «после» паводка — и собирает профиль:
      - snow_km2_by_year:  площадь снега по годам
      - flood_km2_by_year: площадь нового затопления по годам
      - avg_snow_km2:      среднее снега
      - avg_flood_km2:     среднее затопления
      - max_flood_km2:     максимальное затопление за историю
    Годы без снимков пропускаются.
    """
    from analysis import (
        compute_indices, compute_masks, compute_area_km2,
        compute_new_flood, remove_small_objects,
    )
    from scipy.ndimage import zoom as _zoom

    ds_snow  = datetime.strptime(date_snow_start, "%Y-%m-%d")
    de_snow  = datetime.strptime(date_snow_end,   "%Y-%m-%d")
    ds_before = datetime.strptime(date_before, "%Y-%m-%d")
    de_after  = datetime.strptime(date_after,  "%Y-%m-%d")

    snow_by_year  = {}
    flood_by_year = {}

    for offset in range(1, historical_years + 1):
        year = de_after.year - offset
        try:
            ys_snow  = ds_snow.replace(year=ds_snow.year   - offset).strftime("%Y-%m-%d")
            ye_snow  = de_snow.replace(year=de_snow.year   - offset).strftime("%Y-%m-%d")
            ys_before = ds_before.replace(year=ds_before.year - offset).strftime("%Y-%m-%d")
            ye_after  = de_after.replace(year=de_after.year  - offset).strftime("%Y-%m-%d")
        except ValueError:
            continue  # 29 февраля в невисокосный год

        # снег
        snow_data = load_bands(bbox, ys_snow, ye_snow)
        if snow_data is not None:
            ndsi, _ = compute_indices(snow_data["b3"], snow_data["b11"])
            snow_mask, _ = compute_masks(ndsi, ndsi, snow_threshold=snow_threshold)
            snow_mask = remove_small_objects(snow_mask)
            snow_by_year[year] = round(compute_area_km2(snow_mask, snow_data["real_bbox"]), 2)

        # затопление: before vs after
        before_data = load_bands(bbox, ys_before, ys_before)
        if before_data is None:
            d = datetime.strptime(ys_before, "%Y-%m-%d")
            before_data = load_bands(
                bbox,
                (d - timedelta(days=7)).strftime("%Y-%m-%d"),
                (d + timedelta(days=7)).strftime("%Y-%m-%d"),
            )
        after_data = load_bands(bbox, ye_after, ye_after)
        if after_data is None:
            d = datetime.strptime(ye_after, "%Y-%m-%d")
            after_data = load_bands(
                bbox,
                (d - timedelta(days=7)).strftime("%Y-%m-%d"),
                (d + timedelta(days=7)).strftime("%Y-%m-%d"),
            )

        if before_data is not None and after_data is not None:
            _, mndwi_b = compute_indices(before_data["b3"], before_data["b11"])
            _, wb = compute_masks(mndwi_b, mndwi_b,
                                  snow_threshold=snow_threshold,
                                  water_threshold=water_threshold)
            wb = remove_small_objects(wb)

            _, mndwi_a = compute_indices(after_data["b3"], after_data["b11"])
            _, wa = compute_masks(mndwi_a, mndwi_a,
                                  snow_threshold=snow_threshold,
                                  water_threshold=water_threshold)
            wa = remove_small_objects(wa)

            if wb.shape != wa.shape:
                zy = wa.shape[0] / wb.shape[0]
                zx = wa.shape[1] / wb.shape[1]
                wb = (_zoom(wb.astype(float), (zy, zx), order=0) > 0.5).astype(np.uint8)

            flood = compute_new_flood(wb, wa)
            flood = remove_small_objects(flood)
            flood_by_year[year] = round(compute_area_km2(flood, after_data["real_bbox"]), 2)

    snow_vals  = list(snow_by_year.values())
    flood_vals = list(flood_by_year.values())

    return {
        "snow_km2_by_year":  snow_by_year,
        "flood_km2_by_year": flood_by_year,
        "avg_snow_km2":  round(sum(snow_vals)  / len(snow_vals),  2) if snow_vals  else None,
        "avg_flood_km2": round(sum(flood_vals) / len(flood_vals), 2) if flood_vals else None,
        "max_flood_km2": round(max(flood_vals), 2) if flood_vals else None,
    }
