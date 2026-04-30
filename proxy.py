import numpy as np
import rasterio
from rasterio.windows import from_bounds
from rasterio.enums import Resampling
from pystac_client import Client
from pyproj import Transformer
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
import threading

STAC_URL   = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"

app = FastAPI(title="Flood Proxy")
app.mount("/static", StaticFiles(directory="static"), name="static")

# кэш: (item_id, band, bbox_key) -> bytes
_cache: dict = {}
_lock = threading.Lock()


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/proxy/band")
def proxy_band(item_id: str, band: str, bbox: str):
    """
    Скачивает один канал из COG на AWS S3.
    Возвращает float32 массив как бинарные данные.
    Заголовки: x-width, x-height, x-real-bbox
    """
    bbox_key = bbox
    cache_key = (item_id, band, bbox_key)

    with _lock:
        if cache_key in _cache:
            cached = _cache[cache_key]
            return Response(
                content=cached["data"],
                media_type="application/octet-stream",
                headers={
                    "x-width":    str(cached["width"]),
                    "x-height":   str(cached["height"]),
                    "x-real-bbox": cached["real_bbox"],
                    "Access-Control-Expose-Headers": "x-width,x-height,x-real-bbox",
                },
            )

    bbox_list = [float(v) for v in bbox.split(",")]

    client = Client.open(STAC_URL)
    search = client.search(collections=[COLLECTION], ids=[item_id])
    items = list(search.items())
    if not items:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
    item = items[0]

    asset = item.assets.get(band)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"Band {band} not found")

    data_arr, real_bbox = _read_band(asset.href, bbox_list)
    real_bbox_str = ",".join(str(round(v, 6)) for v in real_bbox)
    raw = data_arr.astype(np.float32).tobytes()

    with _lock:
        _cache[cache_key] = {
            "data":     raw,
            "width":    data_arr.shape[1],
            "height":   data_arr.shape[0],
            "real_bbox": real_bbox_str,
        }

    return Response(
        content=raw,
        media_type="application/octet-stream",
        headers={
            "x-width":    str(data_arr.shape[1]),
            "x-height":   str(data_arr.shape[0]),
            "x-real-bbox": real_bbox_str,
            "Access-Control-Expose-Headers": "x-width,x-height,x-real-bbox",
        },
    )



def _read_band(href: str, bbox: list[float]) -> tuple[np.ndarray, list[float]]:
    with rasterio.open(href) as src:
        to_src = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
        minx, miny = to_src.transform(bbox[0], bbox[1])
        maxx, maxy = to_src.transform(bbox[2], bbox[3])
        window = from_bounds(minx, miny, maxx, maxy, src.transform)

        h = window.height
        w = window.width
        level = 0
        while (h / (2 ** level) > 512 or w / (2 ** level) > 512) and level < 4:
            level += 1
        out_h = max(1, int(h / (2 ** level)))
        out_w = max(1, int(w / (2 ** level)))

        data = src.read(1, window=window, out_shape=(out_h, out_w), resampling=Resampling.average).astype(np.float32)

        wt = rasterio.windows.transform(window, src.transform)
        real_minx = wt.c
        real_maxy = wt.f
        real_maxx = real_minx + wt.a * window.width
        real_miny = real_maxy + wt.e * window.height

        to_wgs = Transformer.from_crs(src.crs, "EPSG:4326", always_xy=True)
        lon_min, lat_min = to_wgs.transform(real_minx, real_miny)
        lon_max, lat_max = to_wgs.transform(real_maxx, real_maxy)

    return data, [lon_min, lat_min, lon_max, lat_max]
