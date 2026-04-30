// NDSI = MNDWI = (B3 - B11) / (B3 + B11)
function computeIndex(b3, b11) {
    const n = b3.data.length;
    const result = new Float32Array(n);
    for (let i = 0; i < n; i++) {
        const denom = b3.data[i] + b11.data[i];
        result[i] = denom !== 0 ? (b3.data[i] - b11.data[i]) / denom : 0;
    }
    return { data: result, width: b3.width, height: b3.height, realBbox: b3.realBbox };
}
