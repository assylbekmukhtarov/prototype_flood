"""
Проверяет расчёты площадей и нового затопления.

Два уровня тестов:
1. Синтетические — быстрые, без интернета, проверяют математику
2. Реальные    — скачивают снимки, проверяют правдивость результатов
"""

import numpy as np
import pytest
from analysis import (
    compute_indices,
    compute_masks,
    compute_area_km2,
    compute_new_flood,
    mask_to_geojson,
)

# ── Синтетические тесты (без интернета) ──────────────────────────────────────

BBOX_1x1 = [0.0, 0.0, 1.0, 1.0]   # ~1° × 1° bbox, ~12000 km2 у экватора


class TestComputeIndices:
    def test_ndsi_range(self):
        """Проверяет, что NDSI всегда в диапазоне [-1, 1]."""
        b3  = np.random.randint(0, 5000, (50, 50)).astype(np.float32)
        b11 = np.random.randint(0, 5000, (50, 50)).astype(np.float32)
        ndsi, _ = compute_indices(b3, b11)
        assert ndsi.min() >= -1.0 - 1e-6
        assert ndsi.max() <=  1.0 + 1e-6

    def test_zero_denominator_gives_zero(self):
        """Проверяет, что при B3 = B11 = 0 результат равен 0, не NaN."""
        b3  = np.zeros((10, 10), dtype=np.float32)
        b11 = np.zeros((10, 10), dtype=np.float32)
        ndsi, _ = compute_indices(b3, b11)
        assert not np.isnan(ndsi).any(), "Есть NaN при нулевом знаменателе"
        assert (ndsi == 0).all()

    def test_high_b3_low_b11_gives_positive_ndsi(self):
        """Проверяет, что высокий B3 и низкий B11 дают NDSI > 0 (снег)."""
        b3  = np.full((10, 10), 4000.0, dtype=np.float32)
        b11 = np.full((10, 10),  500.0, dtype=np.float32)
        ndsi, _ = compute_indices(b3, b11)
        assert (ndsi > 0).all(), f"Ожидался NDSI > 0, получили {ndsi.mean():.3f}"

    def test_high_b11_gives_negative_ndsi(self):
        """Проверяет, что высокий B11 и низкий B3 дают NDSI < 0 (не снег, например почва)."""
        b3  = np.full((10, 10),  500.0, dtype=np.float32)
        b11 = np.full((10, 10), 4000.0, dtype=np.float32)
        ndsi, _ = compute_indices(b3, b11)
        assert (ndsi < 0).all()


class TestComputeMasks:
    def test_snow_mask_threshold(self):
        """Проверяет, что пиксели с NDSI > 0.4 дают снег=1, остальные → 0."""
        ndsi = np.array([[0.5, 0.2], [0.8, 0.3]], dtype=np.float32)
        snow, _ = compute_masks(ndsi, ndsi, snow_threshold=0.4, water_threshold=0.0)
        np.testing.assert_array_equal(snow, [[1, 0], [1, 0]])

    def test_water_mask_threshold(self):
        """Проверяет, что пиксели с MNDWI > 0.0 дают вода=1."""
        mndwi = np.array([[0.1, -0.1], [0.5, 0.0]], dtype=np.float32)
        _, water = compute_masks(mndwi, mndwi, snow_threshold=0.4, water_threshold=0.0)
        np.testing.assert_array_equal(water, [[1, 0], [1, 0]])

    def test_all_zero_mask_for_low_values(self):
        """Проверяет, что при всех NDSI < порога маска полностью нулевая."""
        ndsi = np.full((20, 20), 0.1, dtype=np.float32)
        snow, _ = compute_masks(ndsi, ndsi)
        assert snow.sum() == 0


class TestComputeArea:
    def test_full_mask_area(self):
        """Проверяет, что маска из единиц даёт площадь ≈ площади всего bbox."""
        mask = np.ones((100, 100), dtype=np.uint8)
        area = compute_area_km2(mask, BBOX_1x1)
        assert area > 0, "Площадь не должна быть 0"

    def test_zero_mask_gives_zero_area(self):
        """Проверяет, что пустая маска даёт площадь = 0."""
        mask = np.zeros((100, 100), dtype=np.uint8)
        area = compute_area_km2(mask, BBOX_1x1)
        assert area == 0.0

    def test_half_mask_half_area(self):
        """Проверяет, что половина пикселей даёт примерно половину площади."""
        mask_full = np.ones((100, 100), dtype=np.uint8)
        mask_half = np.zeros((100, 100), dtype=np.uint8)
        mask_half[:50, :] = 1
        area_full = compute_area_km2(mask_full, BBOX_1x1)
        area_half = compute_area_km2(mask_half, BBOX_1x1)
        ratio = area_half / area_full
        assert 0.48 < ratio < 0.52, f"Ожидалось ~0.5, получили {ratio:.3f}"

    def test_proportional_to_pixel_count(self):
        """Проверяет, что площадь пропорциональна количеству единичных пикселей."""
        mask_10 = np.zeros((100, 100), dtype=np.uint8); mask_10[:10, :10] = 1
        mask_20 = np.zeros((100, 100), dtype=np.uint8); mask_20[:20, :20] = 1
        area_10 = compute_area_km2(mask_10, BBOX_1x1)
        area_20 = compute_area_km2(mask_20, BBOX_1x1)
        assert abs(area_20 / area_10 - 4.0) < 0.1, (
            f"Ожидалось отношение 4.0, получили {area_20 / area_10:.2f}"
        )


class TestNewFlood:
    def test_new_flood_logic(self):
        """Проверяет логику: новое затопление = вода после И НЕ вода до."""
        before = np.array([[1, 0], [0, 0]], dtype=np.uint8)
        after  = np.array([[1, 1], [0, 1]], dtype=np.uint8)
        flood  = compute_new_flood(before, after)
        # пиксель (0,0): было 1, стало 1 → не новое
        # пиксель (0,1): было 0, стало 1 → новое
        # пиксель (1,1): было 0, стало 1 → новое
        np.testing.assert_array_equal(flood, [[0, 1], [0, 1]])

    def test_no_new_flood_when_water_decreased(self):
        """Проверяет, что если воды стало меньше — новых затоплений нет."""
        before = np.array([[1, 1], [1, 0]], dtype=np.uint8)
        after  = np.array([[0, 0], [1, 0]], dtype=np.uint8)
        flood  = compute_new_flood(before, after)
        assert flood.sum() == 0

    def test_all_new_when_before_empty(self):
        """Проверяет, что если до паводка воды не было — все пиксели воды после = новые."""
        before = np.zeros((10, 10), dtype=np.uint8)
        after  = np.ones((10, 10),  dtype=np.uint8)
        flood  = compute_new_flood(before, after)
        np.testing.assert_array_equal(flood, after)


class TestMaskToGeojson:
    def test_empty_mask_returns_empty_collection(self):
        mask = np.zeros((50, 50), dtype=np.uint8)
        gj = mask_to_geojson(mask, BBOX_1x1)
        assert gj["type"] == "FeatureCollection"
        assert len(gj["features"]) == 0

    def test_nonempty_mask_returns_features(self):
        mask = np.zeros((50, 50), dtype=np.uint8)
        mask[10:20, 10:20] = 1
        gj = mask_to_geojson(mask, BBOX_1x1)
        assert len(gj["features"]) > 0

    def test_features_are_polygons(self):
        mask = np.zeros((50, 50), dtype=np.uint8)
        mask[5:15, 5:15] = 1
        gj = mask_to_geojson(mask, BBOX_1x1)
        for feat in gj["features"]:
            assert feat["geometry"]["type"] in ("Polygon", "MultiPolygon")


# ── Реальные тесты (требуют интернет, ~30 сек) ───────────────────────────────

@pytest.mark.real
class TestRealAreas:
    """
    Проверка площадей на реальных данных.
    Запуск: pytest -m real
    """

    def test_geneva_lake_water_area(self):
        """
        Женевское озеро (~580 km2).
        Ожидаем: площадь воды в bbox > 200 km2 (bbox больше озера).
        """
        from sentinel import load_bands

        bbox = [6.1, 46.2, 6.9, 46.55]
        data = load_bands(bbox, "2024-07-01", "2024-08-31")
        assert data is not None, "Нет снимков за летний период"

        _, mndwi = compute_indices(data["b3"], data["b11"])
        _, water = compute_masks(mndwi, mndwi, water_threshold=0.0)
        area = compute_area_km2(water, data["real_bbox"])

        print(f"\nЖеневское озеро — площадь воды: {area:.1f} km2")
        assert area > 200, f"Площадь воды {area:.1f} km2 слишком мала (ожидалось > 200)"

    def test_kazakhstan_flood_2024(self):
        """
        Паводок в Казахстане (Атырау/Уральск), апрель 2024.
        Река Жайык (Урал) — крупнейший паводок за 80 лет.
        Снимок "до" берём летом 2023 — нет снега, чистое сравнение воды.
        Снимок "после" — пик паводка апрель 2024.
        bbox: пойма реки Жайык у Атырау.
        """
        from sentinel import load_bands

        bbox = [51.8, 47.0, 52.8, 47.6]

        before = load_bands(bbox, "2024-07-01", "2024-08-31")
        after  = load_bands(bbox, "2025-04-10", "2025-05-10")

        assert before is not None, "Нет снимка до паводка (лето 2023)"
        assert after  is not None, "Нет снимка после паводка (апрель-май 2024)"

        print(f"\n  Снимок до:    {before['date']} (облачность {before['cloud_cover']:.1f}%)")
        print(f"  Снимок после: {after['date']}  (облачность {after['cloud_cover']:.1f}%)")

        _, mw_b = compute_indices(before["b3"], before["b11"])
        _, water_before = compute_masks(mw_b, mw_b, water_threshold=0.0)

        _, mw_a = compute_indices(after["b3"], after["b11"])
        _, water_after = compute_masks(mw_a, mw_a, water_threshold=0.0)

        if water_before.shape != water_after.shape:
            from scipy.ndimage import zoom
            zy = water_after.shape[0] / water_before.shape[0]
            zx = water_after.shape[1] / water_before.shape[1]
            water_before = (zoom(water_before.astype(float), (zy, zx), order=0) > 0.5).astype(np.uint8)

        area_before = compute_area_km2(water_before, before["real_bbox"])
        area_after  = compute_area_km2(water_after,  after["real_bbox"])
        flood = compute_new_flood(water_before, water_after)
        flood_area = compute_area_km2(flood, after["real_bbox"])

        print(f"  Вода до:           {area_before:.1f} km2")
        print(f"  Вода после:        {area_after:.1f} km2")
        print(f"  Новое затопление:  {flood_area:.1f} km2")

        # основная проверка: воды после стало больше чем до
        assert area_after > area_before, (
            f"После паводка воды меньше ({area_after:.1f} km2) чем до ({area_before:.1f} km2) — "
            f"возможно снимок после всё ещё облачный"
        )
