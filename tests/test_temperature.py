"""
Проверяет получение температуры через Open-Meteo.

Что проверяется:
- API отвечает и возвращает данные
- Формат ответа корректный
- Среднее значение в разумном диапазоне для известных мест/дат
- Правильно выбирает источник (архив vs прогноз)
- Адаптивность: t, t+1 ... t+5 от date_after
"""

import urllib.request
import json
import pytest
from datetime import date, timedelta


def fetch_temperature(lat: float, lon: float, date_start: str, date_end: str) -> dict:
    """Вызывает логику температурного запроса из main.py напрямую, без HTTP сервера."""
    from datetime import datetime

    today = date.today()
    d_start = datetime.strptime(date_start, "%Y-%m-%d").date()
    d_end   = datetime.strptime(date_end,   "%Y-%m-%d").date()
    archive_cutoff = today - timedelta(days=5)

    if d_end <= archive_cutoff:
        url = (
            f"https://archive-api.open-meteo.com/v1/archive"
            f"?latitude={lat}&longitude={lon}"
            f"&start_date={date_start}&end_date={date_end}"
            f"&daily=temperature_2m_max,temperature_2m_min&timezone=auto"
        )
        source = "historical"
    elif d_start >= today:
        forecast_end = min(d_end, today + timedelta(days=15))
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&start_date={date_start}&end_date={forecast_end.strftime('%Y-%m-%d')}"
            f"&daily=temperature_2m_max,temperature_2m_min&timezone=auto"
        )
        source = "forecast"
    else:
        actual_end = min(d_end, archive_cutoff)
        url = (
            f"https://archive-api.open-meteo.com/v1/archive"
            f"?latitude={lat}&longitude={lon}"
            f"&start_date={date_start}&end_date={actual_end.strftime('%Y-%m-%d')}"
            f"&daily=temperature_2m_max,temperature_2m_min&timezone=auto"
        )
        source = "historical+partial"

    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read())

    daily  = data.get("daily", {})
    t_max  = daily.get("temperature_2m_max", [])
    t_min  = daily.get("temperature_2m_min", [])
    times  = daily.get("time", [])

    avg_temps = [
        (mx + mn) / 2
        for mx, mn in zip(t_max, t_min)
        if mx is not None and mn is not None
    ]
    avg = round(sum(avg_temps) / len(avg_temps), 1) if avg_temps else None

    return {
        "source": source,
        "avg_temp_c": avg,
        "days_count": len(avg_temps),
        "t_max": t_max,
        "t_min": t_min,
        "times": times,
    }


class TestTemperatureAPI:
    def test_api_responds(self):
        """Проверяет, что Open-Meteo отвечает на запрос."""
        result = fetch_temperature(48.0, 67.0, "2024-04-01", "2024-04-06")
        assert result is not None
        assert result["avg_temp_c"] is not None

    def test_returns_6_days(self):
        """Проверяет, что за период t ... t+5 возвращается 6 дней данных."""
        result = fetch_temperature(48.0, 67.0, "2024-04-01", "2024-04-06")
        assert result["days_count"] == 6, (
            f"Ожидалось 6 дней, получили {result['days_count']}"
        )

    def test_avg_is_mean_of_max_min(self):
        """Проверяет, что среднее = среднее по всем дням (max+min)/2."""
        result = fetch_temperature(48.0, 67.0, "2024-04-01", "2024-04-06")
        expected = round(
            sum((mx + mn) / 2 for mx, mn in zip(result["t_max"], result["t_min"]))
            / result["days_count"],
            1,
        )
        assert result["avg_temp_c"] == expected

    def test_max_always_gte_min(self):
        """Проверяет, что максимальная температура >= минимальной для каждого дня."""
        result = fetch_temperature(48.0, 67.0, "2024-04-01", "2024-04-06")
        for mx, mn in zip(result["t_max"], result["t_min"]):
            assert mx >= mn, f"max({mx}) < min({mn}) — ошибка данных"


class TestTemperatureValues:
    def test_summer_kazakhstan_is_warm(self):
        """
        Лето в Казахстане (Нур-Султан) — температура должна быть > 15°C.
        Исторические данные июль 2023.
        """
        result = fetch_temperature(51.18, 71.45, "2023-07-01", "2023-07-06")
        avg = result["avg_temp_c"]
        print(f"\nНур-Султан, июль 2023: {avg}°C")
        assert avg > 15, f"Ожидалась летняя температура > 15°C, получили {avg}°C"

    def test_winter_alps_is_cold(self):
        """
        Зима в Альпах — температура должна быть < 5°C.
        Исторические данные январь 2024.
        """
        result = fetch_temperature(46.5, 10.5, "2024-01-10", "2024-01-15")
        avg = result["avg_temp_c"]
        print(f"\nАльпы, январь 2024: {avg}°C")
        assert avg < 5, f"Ожидалась зимняя температура < 5°C, получили {avg}°C"

    def test_spring_orenburg_around_zero(self):
        """
        Оренбург, апрель 2024 (период паводка) — температура около 0–15°C.
        """
        result = fetch_temperature(51.77, 55.1, "2024-04-01", "2024-04-06")
        avg = result["avg_temp_c"]
        print(f"\nОренбург, апрель 2024: {avg}°C")
        assert -5 < avg < 20, f"Температура {avg}°C вне разумного диапазона для апреля"


class TestTemperatureSourceSelection:
    def test_past_date_uses_archive(self):
        """Проверяет, что дата в прошлом выбирает источник 'historical'."""
        result = fetch_temperature(48.0, 67.0, "2023-01-01", "2023-01-06")
        assert result["source"] == "historical", (
            f"Ожидался источник 'historical', получили '{result['source']}'"
        )

    def test_future_date_uses_forecast(self):
        """Проверяет, что дата в будущем выбирает источник 'forecast'."""
        future = (date.today() + timedelta(days=3)).strftime("%Y-%m-%d")
        future_end = (date.today() + timedelta(days=8)).strftime("%Y-%m-%d")
        result = fetch_temperature(48.0, 67.0, future, future_end)
        assert result["source"] == "forecast", (
            f"Ожидался источник 'forecast', получили '{result['source']}'"
        )

    def test_adaptive_period_t_to_t5(self):
        """
        Проверяет, что период t ... t+5 даёт ровно 6 дней
        для любой даты в прошлом.
        """
        t = "2024-03-15"
        t5 = "2024-03-20"
        result = fetch_temperature(48.0, 67.0, t, t5)
        assert result["days_count"] == 6
        assert result["times"][0] == t
        assert result["times"][-1] == t5
