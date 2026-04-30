function computeMask(index, threshold) {
    const n = index.data.length;
    const mask = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
        mask[i] = index.data[i] > threshold ? 1 : 0;
    }
    return { data: mask, width: index.width, height: index.height, realBbox: index.realBbox };
}

// удаляет связные компоненты меньше minPixels (упрощённый flood fill)
function removeSmallObjects(mask, minPixels = 25) {
    const { data, width, height } = mask;
    const visited = new Uint8Array(data.length);
    const result = new Uint8Array(data);

    for (let i = 0; i < data.length; i++) {
        if (data[i] === 0 || visited[i]) continue;

        const component = [];
        const stack = [i];
        while (stack.length > 0) {
            const idx = stack.pop();
            if (idx < 0 || idx >= data.length || visited[idx] || data[idx] === 0) continue;
            visited[idx] = 1;
            component.push(idx);
            const x = idx % width;
            const y = Math.floor(idx / width);
            if (x > 0)          stack.push(idx - 1);
            if (x < width - 1)  stack.push(idx + 1);
            if (y > 0)          stack.push(idx - width);
            if (y < height - 1) stack.push(idx + width);
        }

        if (component.length < minPixels) {
            for (const idx of component) result[idx] = 0;
        }
    }

    return { data: result, width, height, realBbox: mask.realBbox };
}
