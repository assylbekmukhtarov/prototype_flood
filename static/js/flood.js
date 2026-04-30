// resizes maskBefore to match maskAfter dimensions (nearest neighbour)
function resizeMask(src, targetWidth, targetHeight) {
    const dst = new Uint8Array(targetWidth * targetHeight);
    const scaleY = src.height / targetHeight;
    const scaleX = src.width  / targetWidth;
    for (let y = 0; y < targetHeight; y++) {
        for (let x = 0; x < targetWidth; x++) {
            const sy = Math.min(src.height - 1, Math.floor(y * scaleY));
            const sx = Math.min(src.width  - 1, Math.floor(x * scaleX));
            dst[y * targetWidth + x] = src.data[sy * src.width + sx];
        }
    }
    return { data: dst, width: targetWidth, height: targetHeight, realBbox: src.realBbox };
}

function computeNewFlood(maskBefore, maskAfter) {
    let before = maskBefore;
    if (before.width !== maskAfter.width || before.height !== maskAfter.height) {
        before = resizeMask(before, maskAfter.width, maskAfter.height);
    }

    const n = maskAfter.data.length;
    const result = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
        result[i] = (maskAfter.data[i] === 1 && before.data[i] === 0) ? 1 : 0;
    }
    return { data: result, width: maskAfter.width, height: maskAfter.height, realBbox: maskAfter.realBbox };
}
