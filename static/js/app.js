// ── Map init ──────────────────────────────────────────────────────────────────
const map = L.map("map").setView([48.0, 67.0], 5);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap", maxZoom: 19,
}).addTo(map);

const drawnItems = new L.FeatureGroup().addTo(map);
const drawControl = new L.Control.Draw({
    draw: {
        rectangle: { shapeOptions: { color: "#4f7ef8", weight: 2, fillOpacity: 0.08 } },
        polygon:   { shapeOptions: { color: "#4f7ef8", weight: 2, fillOpacity: 0.08 }, showArea: true },
        polyline: false, circle: false, circlemarker: false, marker: false,
    },
    edit: { featureGroup: drawnItems },
});
map.addControl(drawControl);

let bbox = null;
let lastResult = null;
let snowLayer = null, waterLayer = null, floodLayer = null, rgbLayer = null;

// ── Draw events ───────────────────────────────────────────────────────────────
map.on(L.Draw.Event.CREATED, (e) => {
    drawnItems.clearLayers();
    drawnItems.addLayer(e.layer);
    const b = e.layer.getBounds();
    bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
    onBboxSet();
});

map.on(L.Draw.Event.EDITED, (e) => {
    e.layers.eachLayer(layer => {
        const b = layer.getBounds();
        bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
        document.getElementById("roi-coords").textContent = bbox.map(v => v.toFixed(4)).join(", ");
    });
    fetchTemperatureAuto();
});

map.on(L.Draw.Event.DELETED, () => {
    bbox = null;
    resetUI();
});

function onBboxSet() {
    const status = document.getElementById("roi-status");
    status.className = "has-roi";
    status.textContent = "Область выбрана";
    document.getElementById("roi-coords").textContent = bbox.map(v => v.toFixed(4)).join(", ");
    document.getElementById("btn-analyze").disabled = false;
    document.getElementById("btn-analyze").textContent = "Анализировать";
    document.getElementById("btn-rgb").disabled = false;
    fetchTemperatureAuto();
}

// ── Temperature auto ──────────────────────────────────────────────────────────
function onDateChange() { if (bbox) fetchTemperatureAuto(); }

async function fetchTemperatureAuto() {
    if (!bbox) return;
    const lat = ((bbox[1] + bbox[3]) / 2).toFixed(4);
    const lon = ((bbox[0] + bbox[2]) / 2).toFixed(4);
    const dateStart = document.getElementById("date-after").value;
    const d = new Date(dateStart);
    d.setDate(d.getDate() + 5);
    const dateEnd = d.toISOString().slice(0, 10);

    try {
        const data = await fetchTemperature(parseFloat(lat), parseFloat(lon), dateStart, dateEnd);
        if (data.avg_temp_c !== null) {
            document.getElementById("temp-forecast").value = data.avg_temp_c;
            document.getElementById("chip-val").textContent = data.avg_temp_c;
            const srcLabel = { historical: "архив ERA5", forecast: "прогноз", "historical+partial": "архив ERA5" }[data.source] || data.source;
            document.getElementById("chip-src").textContent = `${srcLabel} · ${dateStart} → ${dateEnd}`;
            document.getElementById("temp-chip").style.display = "flex";
        }
    } catch (_) {}
}

// ── Analysis ──────────────────────────────────────────────────────────────────
async function runAnalysis() {
    if (!bbox) return;

    showLoader(true);
    hideError();
    hideResults();
    clearLayers();

    try {
        const snowThreshold  = parseFloat(document.getElementById("snow-threshold").value);
        const waterThreshold = parseFloat(document.getElementById("water-threshold").value);
        const meltThreshold  = parseFloat(document.getElementById("melt-threshold").value);
        const histYears      = parseInt(document.getElementById("historical-years").value) || 0;
        const tempVal        = document.getElementById("temp-forecast").value;
        const tempForecast   = tempVal !== "" ? parseFloat(tempVal) : null;

        const dateSnowStart = document.getElementById("date-snow-start").value;
        const dateSnowEnd   = document.getElementById("date-snow-end").value;
        const dateBefore    = document.getElementById("date-before").value;
        const dateAfter     = document.getElementById("date-after").value;

        if (dateBefore === dateAfter) throw new Error("date_before и date_after совпадают");

        // 1. поиск снимков параллельно
        const [snowItem, beforeItem0, afterItem0, snowAfterItem0] = await Promise.all([
            searchBestScene(bbox, dateSnowStart, dateSnowEnd),
            searchBestScene(bbox, dateBefore, dateBefore),
            searchBestScene(bbox, dateAfter, dateAfter),
            searchBestScene(bbox, dateAfter, dateAfter),
        ]);

        if (!snowItem) throw new Error("Нет снимков за снежный период с облачностью < 20%");

        // fallback ±7 дней для before/after
        const beforeItem = beforeItem0 ?? await searchBestScene(bbox,
            shiftDate(dateBefore, -7), shiftDate(dateBefore, +7));
        const afterItem = afterItem0 ?? await searchBestScene(bbox,
            shiftDate(dateAfter, -7), shiftDate(dateAfter, +7));
        const snowAfterItem = snowAfterItem0 ?? afterItem;

        if (!beforeItem) throw new Error(`Нет снимков около даты ${dateBefore}`);
        if (!afterItem)  throw new Error(`Нет снимков около даты ${dateAfter}`);

        // 2. загрузка каналов параллельно
        const [snowBands, beforeBands, afterBands, snowAfterBands] = await Promise.all([
            loadBands(snowItem,      bbox),
            loadBands(beforeItem,    bbox),
            loadBands(afterItem,     bbox),
            loadBands(snowAfterItem, bbox),
        ]);

        // 3. индексы и маски
        const ndsi       = computeIndex(snowBands.green,      snowBands.swir16);
        const mndwiBefore = computeIndex(beforeBands.green,   beforeBands.swir16);
        const mndwiAfter  = computeIndex(afterBands.green,    afterBands.swir16);
        const ndsiAfter   = computeIndex(snowAfterBands.green, snowAfterBands.swir16);

        const snowMask   = removeSmallObjects(computeMask(ndsi,        snowThreshold));
        const waterBefore = removeSmallObjects(computeMask(mndwiBefore, waterThreshold));
        const waterAfter  = removeSmallObjects(computeMask(mndwiAfter,  waterThreshold));
        const snowAfterMask = removeSmallObjects(computeMask(ndsiAfter, snowThreshold));

        // 4. площади
        const snowArea        = computeAreaKm2(snowMask);
        const waterAreaBefore = computeAreaKm2(waterBefore);
        const waterAreaAfter  = computeAreaKm2(waterAfter);
        const snowAreaAfter   = computeAreaKm2(snowAfterMask);

        // 5. новое затопление
        const floodMask  = removeSmallObjects(computeNewFlood(waterBefore, waterAfter));
        const floodArea  = computeAreaKm2(floodMask);

        // 6. скорость таяния
        const dSnow  = new Date(dateSnowEnd);
        const dAfter = new Date(dateAfter);
        const days   = Math.max(1, Math.round((dAfter - dSnow) / 86400000));
        const meltRate = Math.max(0, (snowArea - snowAreaAfter) / days);

        // 7. исторический профиль
        const hp = await computeHistoricalProfile(bbox, dateSnowStart, dateSnowEnd, dateBefore, dateAfter, histYears, snowThreshold, waterThreshold);

        // 8. риск
        const risk = assessRisk({
            snowAreaKm2: snowArea,
            meltRate,
            meltRateThreshold: meltThreshold,
            tempForecast,
            avgSnowKm2:    hp?.avg_snow_km2  ?? null,
            avgFloodKm2:   hp?.avg_flood_km2 ?? null,
            maxFloodKm2:   hp?.max_flood_km2 ?? null,
            currentFloodKm2: floodArea,
        });

        // 9. отрисовка
        snowLayer  = addMaskLayer(map, snowMask,   [79,  126, 248, 115]);
        waterLayer = addMaskLayer(map, waterAfter, [6,   182, 212, 115]);
        floodLayer = addMaskLayer(map, floodMask,  [220, 38,  38,  153]);

        lastResult = { snowMask, waterAfter, floodMask };

        // 10. результаты в UI
        renderResults({
            metrics: {
                snow_area_km2:        Math.round(snowArea * 100) / 100,
                water_area_before_km2: Math.round(waterAreaBefore * 100) / 100,
                water_area_after_km2:  Math.round(waterAreaAfter * 100) / 100,
                new_flood_area_km2:    Math.round(floodArea * 100) / 100,
                melt_rate_km2_per_day: Math.round(meltRate * 100) / 100,
            },
            risk,
            historical: hp,
            scenes: {
                snow:   { date: snowBands.date,   cloud_cover: snowBands.cloudCover },
                before: { date: beforeBands.date, cloud_cover: beforeBands.cloudCover },
                after:  { date: afterBands.date,  cloud_cover: afterBands.cloudCover },
            },
        });

        document.getElementById("legend").style.display = "block";
        document.getElementById("results").style.display = "block";
        document.getElementById("btn-csv").disabled = false;

    } catch (e) {
        showError(e.message);
    } finally {
        showLoader(false);
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function shiftDate(dateStr, days) {
    const d = new Date(dateStr);
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
}

function clearLayers() {
    [snowLayer, waterLayer, floodLayer].forEach(l => { if (l) map.removeLayer(l); });
    snowLayer = waterLayer = floodLayer = null;
    ["snow", "water", "flood"].forEach(name => {
        const chk = document.getElementById("chk-" + name);
        if (chk) chk.checked = true;
    });
}

function toggleLayer(name) {
    const layers = { snow: snowLayer, water: waterLayer, flood: floodLayer };
    const layer = layers[name];
    if (!layer) return;
    const chk = document.getElementById("chk-" + name);
    chk.checked ? layer.addTo(map) : map.removeLayer(layer);
}

async function loadRgb() {
    if (!bbox) return;
    const dateStart = document.getElementById("date-after").value;
    const dateEnd = shiftDate(dateStart, 30);
    const bboxStr = bbox.join(",");

    document.getElementById("btn-rgb").textContent = "Загрузка…";
    document.getElementById("btn-rgb").disabled = true;

    try {
        const res = await fetch(`/proxy/rgb?bbox=${bboxStr}&date_start=${dateStart}&date_end=${dateEnd}`);
        if (!res.ok) throw new Error("Нет снимков за указанный период");
        const realBboxStr = res.headers.get("x-real-bbox");
        const [minx, miny, maxx, maxy] = realBboxStr.split(",").map(Number);
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        if (rgbLayer) map.removeLayer(rgbLayer);
        rgbLayer = L.imageOverlay(url, [[miny, minx], [maxy, maxx]], { opacity: 0.85, zIndex: 100 }).addTo(map);
        document.getElementById("btn-rgb").textContent = "Скрыть RGB";
        document.getElementById("btn-rgb").onclick = toggleRgb;
    } catch (e) {
        showError("Ошибка RGB: " + e.message);
    } finally {
        document.getElementById("btn-rgb").disabled = false;
    }
}

function toggleRgb() {
    if (!rgbLayer) return;
    if (map.hasLayer(rgbLayer)) {
        map.removeLayer(rgbLayer);
        document.getElementById("btn-rgb").textContent = "Показать RGB-снимок";
        document.getElementById("btn-rgb").onclick = loadRgb;
    } else {
        rgbLayer.addTo(map);
        document.getElementById("btn-rgb").textContent = "Скрыть RGB";
    }
}

function exportCsv() {
    if (!lastResult) return;
    const m = lastResult.metrics;
    const r = lastResult.risk;
    const rows = [
        ["date_after", "snow_area_km2", "water_area_km2", "new_flood_area_km2", "melt_rate_km2_per_day", "risk_score", "risk_level"],
        [document.getElementById("date-after").value, m.snow_area_km2, m.water_area_after_km2, m.new_flood_area_km2, m.melt_rate_km2_per_day, r.risk_score, r.risk_level],
    ];
    const csv = rows.map(r => r.join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "flood_report.csv"; a.click();
    URL.revokeObjectURL(url);
}

function resetUI() {
    document.getElementById("roi-status").className = "";
    document.getElementById("roi-status").textContent = "Используйте инструменты на карте для выбора области";
    document.getElementById("roi-coords").textContent = "";
    document.getElementById("btn-analyze").disabled = true;
    document.getElementById("btn-analyze").textContent = "Выбрать область на карте";
    document.getElementById("btn-rgb").disabled = true;
    document.getElementById("btn-csv").disabled = true;
    document.getElementById("temp-chip").style.display = "none";
    document.getElementById("temp-forecast").value = "";
    document.getElementById("legend").style.display = "none";
    if (rgbLayer) { map.removeLayer(rgbLayer); rgbLayer = null; }
    clearLayers();
    hideResults();
    hideError();
}

let chartSnow = null, chartFlood = null;

function renderResults(data) {
    const m = data.metrics;
    const r = data.risk;
    const hp = data.historical;

    document.getElementById("m-snow").textContent  = m.snow_area_km2;
    document.getElementById("m-water").textContent = m.water_area_after_km2;
    document.getElementById("m-flood").textContent = m.new_flood_area_km2;
    document.getElementById("m-melt").textContent  = m.melt_rate_km2_per_day;
    document.getElementById("m-hist").textContent  = hp?.avg_snow_km2 ?? "—";

    const lvl = r.risk_level;
    const icon = lvl === "high" ? "!" : lvl === "medium" ? "^" : "ok";
    const riskEl = document.getElementById("risk-card");
    riskEl.className = `risk-card risk-${lvl}`;
    riskEl.innerHTML = `
        <div class="risk-header">
          <span>${icon} ${r.risk_label}</span>
          <span class="risk-badge badge-${lvl}">${r.risk_score}%</span>
        </div>
        <div class="risk-score-bar">
          <div class="risk-score-fill fill-${lvl}" style="width:${r.risk_score}%"></div>
        </div>
        <div class="risk-score-label">Индекс риска: ${r.risk_score} / 100</div>`;

    const factorEl = document.getElementById("factor-list");
    factorEl.innerHTML = "";
    for (const [, f] of Object.entries(r.factors)) {
        const li = document.createElement("li");
        const scoreStr = f.score !== null
            ? `<span class="factor-score">${f.score} / 100</span>`
            : `<span class="factor-score" style="color:#9ca3af">н/д</span>`;
        let detail = "";
        if (f.value !== null && f.value !== undefined) {
            detail = ` · ${parseFloat(f.value).toFixed(1)}`;
            if (f.reference !== null && f.reference !== undefined)
                detail += ` / ref ${parseFloat(f.reference).toFixed(1)}`;
        }
        li.innerHTML = `<span>${f.label}${detail}</span>${scoreStr}`;
        factorEl.appendChild(li);
    }

    const chartSection = document.getElementById("chart-section");
    if (hp && (hp.snow_km2_by_year || hp.flood_km2_by_year)) {
        chartSection.style.display = "block";
        const snowByYear  = hp.snow_km2_by_year  || {};
        const floodByYear = hp.flood_km2_by_year || {};
        const allYears = [...new Set([...Object.keys(snowByYear), ...Object.keys(floodByYear)])].sort();

        if (chartSnow)  { chartSnow.destroy();  chartSnow  = null; }
        if (chartFlood) { chartFlood.destroy(); chartFlood = null; }

        chartSnow = new Chart(document.getElementById("chart-snow"), {
            type: "bar",
            data: { labels: allYears, datasets: [{ label: "Снег, км²", data: allYears.map(y => snowByYear[y] ?? null), backgroundColor: "rgba(79,126,248,0.6)", borderColor: "#4f7ef8", borderWidth: 1 }] },
            options: { responsive: true, plugins: { legend: { display: false }, title: { display: true, text: "Площадь снега по годам" } }, scales: { y: { beginAtZero: true } } },
        });
        chartFlood = new Chart(document.getElementById("chart-flood"), {
            type: "bar",
            data: { labels: allYears, datasets: [{ label: "Затопление, км²", data: allYears.map(y => floodByYear[y] ?? null), backgroundColor: "rgba(220,38,38,0.55)", borderColor: "#dc2626", borderWidth: 1 }] },
            options: { responsive: true, plugins: { legend: { display: false }, title: { display: true, text: "Новое затопление по годам" } }, scales: { y: { beginAtZero: true } } },
        });
    } else {
        chartSection.style.display = "none";
    }

    const scenesEl = document.getElementById("scenes-list");
    scenesEl.innerHTML = "";
    const sceneLabels = { snow: "Снежный период", before: "До паводка", after: "После паводка" };
    for (const [key, sc] of Object.entries(data.scenes)) {
        const li = document.createElement("li");
        const dot = document.createElement("div");
        dot.className = "dot ok";
        li.appendChild(dot);
        li.appendChild(document.createTextNode(
            `${sceneLabels[key] || key}: ${sc.date} (облачность ${sc.cloud_cover != null ? sc.cloud_cover.toFixed(1) : "—"}%)`
        ));
        scenesEl.appendChild(li);
    }
}

function showLoader(v) {
    document.getElementById("loader").style.display = v ? "block" : "none";
    document.getElementById("btn-analyze").disabled = v;
}
function showError(msg) {
    const el = document.getElementById("error-msg");
    el.textContent = "! " + msg;
    el.style.display = "block";
}
function hideError()   { document.getElementById("error-msg").style.display = "none"; }
function hideResults() { document.getElementById("results").style.display = "none"; }
