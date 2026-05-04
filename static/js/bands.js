// кэш: "itemId|band|bbox" -> { width, height, data: Float32Array, realBbox }
const _bandCache = {};

function _getProj4(epsgCode) {
    if (epsgCode >= 32601 && epsgCode <= 32660)
        return `+proj=utm +zone=${epsgCode - 32600} +datum=WGS84 +units=m +no_defs`;
    if (epsgCode >= 32701 && epsgCode <= 32760)
        return `+proj=utm +zone=${epsgCode - 32700} +south +datum=WGS84 +units=m +no_defs`;
    throw new Error(`Unsupported EPSG: ${epsgCode}`);
}

async function _readCog(href, bbox) {
    const tiff  = await GeoTIFF.fromUrl(href);
    const image = await tiff.getImage();

    const epsg = image.geoKeys?.ProjectedCSTypeGeoKey;
    if (!epsg) throw new Error("No projection info in GeoTIFF");

    const wgs84  = "+proj=longlat +datum=WGS84 +no_defs";
    const imgCrs = _getProj4(epsg);

    const [lonMin, latMin, lonMax, latMax] = bbox;
    const [xMin, yMin] = proj4(wgs84, imgCrs, [lonMin, latMin]);
    const [xMax, yMax] = proj4(wgs84, imgCrs, [lonMax, latMax]);

    const origin = image.getOrigin();     // [xOrig, yOrig] top-left
    const res    = image.getResolution(); // [xRes, yRes]  yRes < 0
    const imgW   = image.getWidth();
    const imgH   = image.getHeight();

    // bbox → pixel window
    const col0 = Math.max(0,    Math.floor((Math.min(xMin, xMax) - origin[0]) / res[0]));
    const col1 = Math.min(imgW, Math.ceil( (Math.max(xMin, xMax) - origin[0]) / res[0]));
    const row0 = Math.max(0,    Math.floor((Math.max(yMin, yMax) - origin[1]) / res[1]));
    const row1 = Math.min(imgH, Math.ceil( (Math.min(yMin, yMax) - origin[1]) / res[1]));

    const winW = col1 - col0;
    const winH = row1 - row0;
    if (winW <= 0 || winH <= 0) throw new Error("Bbox outside image extent");

    // даунсемплинг до ≤ 512px (как в Python-бэкенде)
    let level = 0;
    while ((winW / (2 ** level) > 512 || winH / (2 ** level) > 512) && level < 4) level++;
    const outW = Math.max(1, Math.round(winW / (2 ** level)));
    const outH = Math.max(1, Math.round(winH / (2 ** level)));

    const [raster] = await image.readRasters({
        window: [col0, row0, col1, row1],
        width:  outW,
        height: outH,
        samples: [0],
        resampleMethod: "bilinear",
    });

    const data = new Float32Array(raster);

    // реальный bbox в WGS84 по краям пикселей
    const realXMin = origin[0] + col0 * res[0];
    const realXMax = origin[0] + col1 * res[0];
    const realYMax = origin[1] + row0 * res[1]; // res[1] < 0
    const realYMin = origin[1] + row1 * res[1];

    const [rLonMin, rLatMin] = proj4(imgCrs, wgs84, [realXMin, realYMin]);
    const [rLonMax, rLatMax] = proj4(imgCrs, wgs84, [realXMax, realYMax]);

    return { width: outW, height: outH, data, realBbox: [rLonMin, rLatMin, rLonMax, rLatMax] };
}

async function fetchBand(item, bandName, bbox) {
    const bboxKey  = bbox.map(v => v.toFixed(4)).join(",");
    const cacheKey = `${item.id}|${bandName}|${bboxKey}`;

    if (_bandCache[cacheKey]) {
        console.log(`[bands] CACHE HIT ${bandName}`);
        return _bandCache[cacheKey];
    }

    const asset = item.assets?.[bandName];
    if (!asset?.href) throw new Error(`Asset "${bandName}" not found in item ${item.id}`);

    const result = await _readCog(asset.href, bbox);
    _bandCache[cacheKey] = result;
    console.log(`[bands] ${bandName} ${result.height}×${result.width} loaded`);
    return result;
}

async function loadBands(item, bbox) {
    const [green, swir16] = await Promise.all([
        fetchBand(item, "green",  bbox),
        fetchBand(item, "swir16", bbox),
    ]);
    return {
        green, swir16,
        date:       item.properties.datetime?.slice(0, 10),
        cloudCover: item.properties["eo:cloud_cover"],
    };
}
