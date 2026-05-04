// Каналы Sentinel-2 L2A для экспорта
const EXPORT_BANDS = [
    "blue",      // B02  10m
    "green",     // B03  10m
    "red",       // B04  10m
    "rededge1",  // B05  20m
    "rededge2",  // B06  20m
    "rededge3",  // B07  20m
    "nir",       // B08  10m
    "nir08",     // B08A 20m
    "swir16",    // B11  20m
    "swir22",    // B12  20m
];

async function downloadGeoTiff(item, bbox) {
    // Берём только каналы, которые есть в этом снимке
    const available = EXPORT_BANDS.filter(b => item.assets?.[b]?.href);

    const settled = await Promise.allSettled(
        available.map(b => fetchBand(item, b, bbox).then(r => ({ ...r, name: b })))
    );

    const bands = settled
        .filter(r => r.status === "fulfilled")
        .map(r => r.value);

    if (bands.length === 0) throw new Error("Нет доступных каналов");

    // Нормируем размеры к первому каналу (ближайший сосед)
    const W = bands[0].width;
    const H = bands[0].height;
    const realBbox = bands[0].realBbox;

    const arrays = bands.map(band => {
        const out = new Uint16Array(W * H);
        if (band.width === W && band.height === H) {
            for (let i = 0; i < band.data.length; i++)
                out[i] = Math.max(0, Math.round(band.data[i]));
        } else {
            for (let y = 0; y < H; y++) {
                for (let x = 0; x < W; x++) {
                    const sx = Math.floor(x * band.width / W);
                    const sy = Math.floor(y * band.height / H);
                    out[y * W + x] = Math.max(0, Math.round(band.data[sy * band.width + sx]));
                }
            }
        }
        return out;
    });

    const [lonMin, latMin, lonMax, latMax] = realBbox;
    const xScale = (lonMax - lonMin) / W;
    const yScale = (latMax - latMin) / H;

    const buffer = await GeoTIFF.writeArrayBuffer(arrays, {
        width:          W,
        height:         H,
        SamplesPerPixel: arrays.length,
        BitsPerSample:  arrays.map(() => 16),
        SampleFormat:   arrays.map(() => 1),   // 1 = unsigned int
        ModelPixelScale: [xScale, yScale, 0],
        ModelTiepoint:   [0, 0, 0, lonMin, latMax, 0],
        GeographicTypeGeoKey: 4326,
        GTModelTypeGeoKey:    2,
        GTRasterTypeGeoKey:   1,
    });

    const date = item.properties.datetime?.slice(0, 10) ?? "unknown";
    const blob = new Blob([buffer], { type: "image/tiff" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href     = url;
    a.download = `sentinel2_${date}.tif`;
    a.click();
    URL.revokeObjectURL(url);

    return bands.length;
}
