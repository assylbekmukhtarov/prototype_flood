// кэш: "itemId|band|bbox" -> { width, height, data: Float32Array }
const _bandCache = {};

async function fetchBand(itemId, bandName, bbox) {
    const bboxKey = bbox.map(v => v.toFixed(4)).join(",");
    const cacheKey = `${itemId}|${bandName}|${bboxKey}`;

    if (_bandCache[cacheKey]) {
        console.log(`[bands] CACHE HIT ${bandName}`);
        return _bandCache[cacheKey];
    }

    const params = new URLSearchParams({
        item_id: itemId,
        band: bandName,
        bbox: bboxKey,
    });

    const res = await fetch(`/proxy/band?${params}`);
    if (!res.ok) throw new Error(`proxy/band error: ${res.status}`);

    const width  = parseInt(res.headers.get("x-width"));
    const height = parseInt(res.headers.get("x-height"));
    const realBbox = res.headers.get("x-real-bbox").split(",").map(Number);

    const buffer = await res.arrayBuffer();
    const data = new Float32Array(buffer);

    const result = { width, height, data, realBbox };
    _bandCache[cacheKey] = result;
    console.log(`[bands] ${bandName} ${height}x${width} loaded`);
    return result;
}

async function loadBands(item, bbox) {
    const [green, swir16] = await Promise.all([
        fetchBand(item.id, "green",  bbox),
        fetchBand(item.id, "swir16", bbox),
    ]);
    return { green, swir16, date: item.properties.datetime?.slice(0, 10), cloudCover: item.properties["eo:cloud_cover"] };
}
