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
