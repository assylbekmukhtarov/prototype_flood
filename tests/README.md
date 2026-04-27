# Тесты

Тесты не трогают основной код — только импортируют модули из родительской папки.

## Структура

```
tests/
├── conftest.py          # добавляет родительскую папку в sys.path
├── test_sentinel.py     # загрузка снимков Sentinel-2
├── test_analysis.py     # площади, маски, новое затопление
├── test_temperature.py  # температура из Open-Meteo
└── README.md
```

## Запуск

```bash
cd prototype_flood

# быстрые тесты (без интернета, только математика) — ~2 сек
venv/Scripts/python.exe -m pytest tests/ -m "not real" -v

# только температура
venv/Scripts/python.exe -m pytest tests/test_temperature.py -v

# только снимки (нужен интернет, ~30 сек)
venv/Scripts/python.exe -m pytest tests/test_sentinel.py -v

# реальные тесты площадей и паводка (нужен интернет, ~60 сек)
venv/Scripts/python.exe -m pytest tests/test_analysis.py -m real -v -s

# все тесты сразу
venv/Scripts/python.exe -m pytest tests/ -v -s
```

## Что проверяется

### test_sentinel.py — снимки
| Тест | Что проверяет |
|---|---|
| `test_finds_items` | STAC-каталог находит снимки |
| `test_item_has_required_assets` | Есть каналы B3 и B11 |
| `test_cloud_cover_below_threshold` | Облачность < 20% |
| `test_pixel_values_range` | Пиксели в диапазоне 0–12000 |
| `test_snow_physics` | На снежном снимке B3 > B11 |
| `test_real_bbox_close_to_request` | bbox не сдвинут более чем на 0.1° |

### test_analysis.py — расчёты
| Тест | Что проверяет |
|---|---|
| `test_ndsi_range` | NDSI всегда в [-1, 1] |
| `test_zero_denominator_gives_zero` | Нет NaN при B3=B11=0 |
| `test_half_mask_half_area` | Площадь пропорциональна пикселям |
| `test_new_flood_logic` | Логика нового затопления верна |
| `test_geneva_lake_water_area` ⚠️ | Женевское озеро > 200 км² |
| `test_orenburg_flood_2024` ⚠️ | Паводок Оренбург > 10 км² |

⚠️ — помечены `@pytest.mark.real`, требуют интернет

### test_temperature.py — температура
| Тест | Что проверяет |
|---|---|
| `test_returns_6_days` | t, t+1, ..., t+5 = ровно 6 дней |
| `test_summer_kazakhstan_is_warm` | Лето > 15°C (Нур-Султан июль) |
| `test_winter_alps_is_cold` | Зима < 5°C (Альпы январь) |
| `test_past_date_uses_archive` | Прошлое → ERA5 архив |
| `test_future_date_uses_forecast` | Будущее → прогноз |
