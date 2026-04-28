"""
Проверяет загрузку снимков Sentinel-2 через STAC + COG.

Что проверяется:
- STAC-каталог отвечает и находит снимки
- Загружаются оба канала B3 и B11
- Значения пикселей в разумном диапазоне (uint16: 0–65535)
- На снежном снимке B3 >> B11 (физика: снег отражает зелёный, поглощает SWIR)
- real_bbox возвращается и близок к запрошенному
"""

import numpy as np
import pytest
from sentinel import load_bands, search_best_scene

# Альпы — гарантированно есть снег зимой, хорошая покрытость Sentinel-2
SNOW_BBOX   = [10.0, 46.0, 11.5, 47.0]
SNOW_START  = "2025-01-15"
SNOW_END    = "2025-02-28"

# Женевское озеро летом — вода без снега
WATER_BBOX  = [6.1, 46.2, 6.9, 46.55]
WATER_START = "2024-07-01"
WATER_END   = "2024-08-31"


class TestStacSearch:
    def test_finds_items(self):
        """Проверяет, что STAC-каталог возвращает хотя бы один снимок за период."""
        item = search_best_scene(SNOW_BBOX, SNOW_START, SNOW_END)
        assert item is not None, "Нет снимков за снежный период — проверьте даты или bbox"

    def test_item_has_required_assets(self):
        """Проверяет, что у найденного снимка есть каналы green и swir16."""
        item = search_best_scene(SNOW_BBOX, SNOW_START, SNOW_END)
        assert item is not None
        assert "green"  in item.assets, "Нет канала green (B3)"
        assert "swir16" in item.assets, "Нет канала swir16 (B11)"

    def test_cloud_cover_below_threshold(self):
        """Проверяет, что облачность найденного снимка < 20%."""
        item = search_best_scene(SNOW_BBOX, SNOW_START, SNOW_END)
        assert item is not None
        cloud = item.properties.get("eo:cloud_cover", 999)
        assert cloud < 20, f"Облачность {cloud}% превышает порог 20%"

    def test_no_items_for_impossible_dates(self):
        """Проверяет, что за несуществующий диапазон дат возвращается None."""
        item = search_best_scene([0.0, 0.0, 0.1, 0.1], "2099-01-01", "2099-01-02")
        assert item is None


class TestBandLoading:
    @pytest.fixture(scope="class")
    def snow_data(self):
        data = load_bands(SNOW_BBOX, SNOW_START, SNOW_END)
        assert data is not None, "Не удалось загрузить снежный снимок"
        return data

    def test_bands_loaded(self, snow_data):
        """Проверяет, что оба канала загружены и не пустые."""
        assert snow_data["b3"]  is not None
        assert snow_data["b11"] is not None
        assert snow_data["b3"].size  > 0
        assert snow_data["b11"].size > 0

    def test_same_shape(self, snow_data):
        """Проверяет, что B3 и B11 приведены к одному размеру."""
        assert snow_data["b3"].shape == snow_data["b11"].shape, (
            f"Разные размеры: B3={snow_data['b3'].shape}, B11={snow_data['b11'].shape}"
        )

    def test_pixel_values_range(self, snow_data):
        """Значения пикселей в диапазоне uint16 Sentinel-2 (0–65535).
        Снег и облака могут давать значения выше 10000 — это норма для L2A.
        """
        assert snow_data["b3"].min()  >= 0
        assert snow_data["b3"].max()  <= 65535, f"B3 max={snow_data['b3'].max()} — выше uint16"
        assert snow_data["b11"].min() >= 0
        assert snow_data["b11"].max() <= 65535, f"B11 max={snow_data['b11'].max()} — выше uint16"

    def test_snow_physics(self, snow_data):
        """
        Физическая проверка: на снежном снимке средний B3 > средний B11.
        Снег сильно отражает зелёный (B3) и поглощает SWIR (B11).
        """
        mean_b3  = snow_data["b3"].mean()
        mean_b11 = snow_data["b11"].mean()
        assert mean_b3 > mean_b11, (
            f"Ожидалось B3({mean_b3:.1f}) > B11({mean_b11:.1f}), "
            f"но это не так — возможно снег не покрывает bbox"
        )

    def test_real_bbox_returned(self, snow_data):
        """Проверяет, что real_bbox возвращается и содержит 4 координаты."""
        rb = snow_data["real_bbox"]
        assert len(rb) == 4
        lon_min, lat_min, lon_max, lat_max = rb
        assert lon_min < lon_max, "real_bbox: lon_min >= lon_max"
        assert lat_min < lat_max, "real_bbox: lat_min >= lat_max"

    def test_real_bbox_close_to_request(self, snow_data):
        """Проверяет, что real_bbox не сильно отличается от запрошенного (< 0.1°)."""
        rb = snow_data["real_bbox"]
        req = SNOW_BBOX
        for r, q in zip(rb, req):
            assert abs(r - q) < 0.1, f"real_bbox сильно отличается: {rb} vs {req}"

    def test_date_returned(self, snow_data):
        """Проверяет, что дата снимка возвращается в формате YYYY-MM-DD."""
        import re
        assert re.match(r"\d{4}-\d{2}-\d{2}", snow_data["date"]), (
            f"Неверный формат даты: {snow_data['date']}"
        )
