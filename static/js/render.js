function buildRgb(b4, b3, b2) {
    const n = b4.data.length;
    const canvas = document.createElement("canvas");
    canvas.width  = b4.width;
    canvas.height = b4.height;
    const ctx = canvas.getContext("2d");
    const img = ctx.createImageData(b4.width, b4.height);

    function percentile(arr, p) {
        const sorted = Float32Array.from(arr).sort();
        return sorted[Math.floor(sorted.length * p / 100)];
    }
    function norm(band) {
        const p2  = percentile(band.data, 2);
        const p98 = percentile(band.data, 98);
        const range = p98 - p2 || 1e-6;
        const out = new Uint8Array(band.data.length);
        for (let i = 0; i < band.data.length; i++) {
            out[i] = Math.min(255, Math.max(0, ((band.data[i] - p2) / range) * 255));
        }
        return out;
    }

    const r = norm(b4);
    const g = norm(b3);
    const b = norm(b2);

    for (let i = 0; i < n; i++) {
        img.data[i * 4]     = r[i];
        img.data[i * 4 + 1] = g[i];
        img.data[i * 4 + 2] = b[i];
        img.data[i * 4 + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    return { canvas, realBbox: b4.realBbox };
}

function addRgbLayer(map, b4, b3, b2) {
    const { canvas, realBbox } = buildRgb(b4, b3, b2);
    const url = canvas.toDataURL();
    const [lonMin, latMin, lonMax, latMax] = realBbox;
    return L.imageOverlay(url, [[latMin, lonMin], [latMax, lonMax]], { opacity: 0.85, zIndex: 100 });
}

function maskToCanvas(mask, color) {
    const canvas = document.createElement("canvas");
    canvas.width  = mask.width;
    canvas.height = mask.height;
    const ctx = canvas.getContext("2d");
    const img = ctx.createImageData(mask.width, mask.height);

    const [r, g, b, a] = color;
    for (let i = 0; i < mask.data.length; i++) {
        if (mask.data[i] === 1) {
            img.data[i * 4]     = r;
            img.data[i * 4 + 1] = g;
            img.data[i * 4 + 2] = b;
            img.data[i * 4 + 3] = a;
        }
    }
    ctx.putImageData(img, 0, 0);
    return canvas;
}

function addMaskLayer(map, mask, color) {
    const canvas = maskToCanvas(mask, color);
    const url = canvas.toDataURL();
    const [lonMin, latMin, lonMax, latMax] = mask.realBbox;
    return L.imageOverlay(url, [[latMin, lonMin], [latMax, lonMax]], { opacity: 1 }).addTo(map);
}
