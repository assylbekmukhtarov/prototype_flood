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


def _resize(arr: np.ndarray, target_shape: tuple) -> np.ndarray:
    """Изменяет размер массива до target_shape через scipy.ndimage.zoom."""
    from rasterio.transform import from_bounds
    import rasterio.transform
    zoom_y = target_shape[0] / arr.shape[0]
    zoom_x = target_shape[1] / arr.shape[1]
    from scipy.ndimage import zoom
    return zoom(arr, (zoom_y, zoom_x), order=1).astype(np.float32)
