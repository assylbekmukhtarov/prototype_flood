async function fetchTemperature(lat, lon, dateStart, dateEnd) {
    const today = new Date().toISOString().slice(0, 10);
    const archiveCutoff = new Date(Date.now() - 5 * 86400000).toISOString().slice(0, 10);

    let url, source;

    if (dateEnd <= archiveCutoff) {
        url = `https://archive-api.open-meteo.com/v1/archive?latitude=${lat}&longitude=${lon}&start_date=${dateStart}&end_date=${dateEnd}&daily=temperature_2m_max,temperature_2m_min&timezone=auto`;
        source = "historical";
    } else if (dateStart >= today) {
        const forecastEnd = dateEnd < new Date(Date.now() + 15 * 86400000).toISOString().slice(0, 10) ? dateEnd : new Date(Date.now() + 15 * 86400000).toISOString().slice(0, 10);
        url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&start_date=${dateStart}&end_date=${forecastEnd}&daily=temperature_2m_max,temperature_2m_min&timezone=auto`;
        source = "forecast";
    } else {
        const actualEnd = dateEnd < archiveCutoff ? dateEnd : archiveCutoff;
        url = `https://archive-api.open-meteo.com/v1/archive?latitude=${lat}&longitude=${lon}&start_date=${dateStart}&end_date=${actualEnd}&daily=temperature_2m_max,temperature_2m_min&timezone=auto`;
        source = "historical+partial";
    }

    const res = await fetch(url);
    if (!res.ok) throw new Error(`Open-Meteo error: ${res.status}`);
    const data = await res.json();

    const tMax = data.daily?.temperature_2m_max ?? [];
    const tMin = data.daily?.temperature_2m_min ?? [];
    const times = data.daily?.time ?? [];

    const avgTemps = tMax.map((mx, i) => (mx + tMin[i]) / 2).filter(v => !isNaN(v));
    const avg = avgTemps.length > 0 ? Math.round(avgTemps.reduce((a, b) => a + b, 0) / avgTemps.length * 10) / 10 : null;

    return { source, avg_temp_c: avg, days_count: avgTemps.length, times, t_max: tMax, t_min: tMin };
}
