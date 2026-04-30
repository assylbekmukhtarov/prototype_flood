async function computeHistoricalProfile(bbox, dateSnowStart, dateSnowEnd, dateBefore, dateAfter, years, snowThreshold, waterThreshold) {
    if (years === 0) return null;

    const snowByYear  = {};
    const floodByYear = {};

    const yearOffsets = Array.from({ length: years }, (_, i) => i + 1);

    await Promise.all(yearOffsets.map(async (offset) => {
        const shiftDate = (d, years) => {
            const dt = new Date(d);
            dt.setFullYear(dt.getFullYear() - years);
            return dt.toISOString().slice(0, 10);
        };

        const year = new Date(dateAfter).getFullYear() - offset;
        try {
            const ysSnow   = shiftDate(dateSnowStart, offset);
            const yeSnow   = shiftDate(dateSnowEnd,   offset);
            const ysBefore = shiftDate(dateBefore,    offset);
            const yeAfter  = shiftDate(dateAfter,     offset);

            // снег
            const snowItem = await searchBestScene(bbox, ysSnow, yeSnow);
            if (snowItem) {
                const bands = await loadBands(snowItem, bbox);
                const idx   = computeIndex(bands.green, bands.swir16);
                const mask  = removeSmallObjects(computeMask(idx, snowThreshold));
                snowByYear[year] = Math.round(computeAreaKm2(mask) * 100) / 100;
            }

            // затопление
            let beforeItem = await searchBestScene(bbox, ysBefore, ysBefore);
            if (!beforeItem) {
                const d = new Date(ysBefore);
                beforeItem = await searchBestScene(
                    bbox,
                    new Date(d - 7*86400000).toISOString().slice(0,10),
                    new Date(+d + 7*86400000).toISOString().slice(0,10),
                );
            }
            let afterItem = await searchBestScene(bbox, yeAfter, yeAfter);
            if (!afterItem) {
                const d = new Date(yeAfter);
                afterItem = await searchBestScene(
                    bbox,
                    new Date(+d - 7*86400000).toISOString().slice(0,10),
                    new Date(+d + 7*86400000).toISOString().slice(0,10),
                );
            }

            if (beforeItem && afterItem) {
                const [bBands, aBands] = await Promise.all([
                    loadBands(beforeItem, bbox),
                    loadBands(afterItem,  bbox),
                ]);
                const wb = removeSmallObjects(computeMask(computeIndex(bBands.green, bBands.swir16), waterThreshold));
                const wa = removeSmallObjects(computeMask(computeIndex(aBands.green, aBands.swir16), waterThreshold));
                const flood = removeSmallObjects(computeNewFlood(wb, wa));
                floodByYear[year] = Math.round(computeAreaKm2(flood) * 100) / 100;
            }
        } catch (e) {
            console.warn(`[history] year ${year} skipped:`, e.message);
        }
    }));

    const snowVals  = Object.values(snowByYear);
    const floodVals = Object.values(floodByYear);

    return {
        snow_km2_by_year:  snowByYear,
        flood_km2_by_year: floodByYear,
        avg_snow_km2:  snowVals.length  ? Math.round(snowVals.reduce((a,b)=>a+b,0)  / snowVals.length  * 100) / 100 : null,
        avg_flood_km2: floodVals.length ? Math.round(floodVals.reduce((a,b)=>a+b,0) / floodVals.length * 100) / 100 : null,
        max_flood_km2: floodVals.length ? Math.round(Math.max(...floodVals) * 100) / 100 : null,
    };
}
