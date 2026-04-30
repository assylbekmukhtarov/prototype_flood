function assessRisk({ snowAreaKm2, meltRate, meltRateThreshold, tempForecast, avgSnowKm2, avgFloodKm2, maxFloodKm2, currentFloodKm2 }) {
    let weightedSum = 0;
    let totalWeight = 0;
    const factors = {};

    // фактор 1: снег vs историческое среднее (40%)
    const w1 = 40;
    if (avgSnowKm2 != null && avgSnowKm2 > 0) {
        const ratio = snowAreaKm2 / avgSnowKm2;
        const score1 = Math.min(100, Math.max(0, Math.tanh(ratio - 1.0 + 0.5) * 100 + 50));
        factors.snow_vs_avg = { label: "Снег vs историческое среднее", value: snowAreaKm2, reference: avgSnowKm2, score: Math.round(score1 * 10) / 10, weight: w1 };
        weightedSum += score1 * w1;
        totalWeight  += w1;
    } else {
        factors.snow_vs_avg = { label: "Снег vs историческое среднее", value: snowAreaKm2, reference: null, score: null, weight: w1 };
    }

    // фактор 2: скорость таяния (25%)
    const w2 = 25;
    const score2 = meltRateThreshold > 0 ? Math.min(100, (meltRate / meltRateThreshold) * 100) : (meltRate > 0 ? 100 : 0);
    factors.melt_rate = { label: "Скорость таяния", value: Math.round(meltRate * 100) / 100, reference: meltRateThreshold, score: Math.round(score2 * 10) / 10, weight: w2 };
    weightedSum += score2 * w2;
    totalWeight  += w2;

    // фактор 3: температура (20%)
    const w3 = 20;
    if (tempForecast != null) {
        const score3 = Math.min(100, Math.max(0, 50 + tempForecast * 5));
        factors.temperature = { label: "Прогноз температуры", value: tempForecast, reference: 0, score: Math.round(score3 * 10) / 10, weight: w3 };
        weightedSum += score3 * w3;
        totalWeight  += w3;
    } else {
        factors.temperature = { label: "Прогноз температуры", value: null, reference: 0, score: null, weight: w3 };
    }

    // фактор 4: затопление vs исторический максимум (15%)
    const w4 = 15;
    if (maxFloodKm2 != null && maxFloodKm2 > 0) {
        const score4 = Math.min(100, (currentFloodKm2 / maxFloodKm2) * 100);
        factors.flood_vs_max = { label: "Затопление vs исторический максимум", value: currentFloodKm2, reference: maxFloodKm2, avg_flood_km2: avgFloodKm2, score: Math.round(score4 * 10) / 10, weight: w4 };
        weightedSum += score4 * w4;
        totalWeight  += w4;
    } else {
        factors.flood_vs_max = { label: "Затопление vs исторический максимум", value: currentFloodKm2, reference: null, avg_flood_km2: avgFloodKm2, score: null, weight: w4 };
    }

    const riskScore = totalWeight > 0 ? Math.round(weightedSum / totalWeight * 10) / 10 : 0;
    const level = riskScore < 30 ? "low" : riskScore < 70 ? "medium" : "high";
    const label = level === "low" ? "Низкий риск" : level === "medium" ? "Средний риск" : "Высокий риск";

    return { risk_score: riskScore, risk_level: level, risk_label: label, factors, high_risk: level === "high" };
}
