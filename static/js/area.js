function computeAreaKm2(mask) {
    const [lonMin, latMin, lonMax, latMax] = mask.realBbox;
    const latCenter = (latMin + latMax) / 2;

    const kmPerLat = 111.0;
    const kmPerLon = 111.0 * Math.cos(latCenter * Math.PI / 180);

    const pixelHeightKm = (latMax - latMin) * kmPerLat / mask.height;
    const pixelWidthKm  = (lonMax - lonMin) * kmPerLon / mask.width;
    const pixelKm2 = pixelHeightKm * pixelWidthKm;

    let count = 0;
    for (let i = 0; i < mask.data.length; i++) {
        if (mask.data[i] === 1) count++;
    }

    return count * pixelKm2;
}
