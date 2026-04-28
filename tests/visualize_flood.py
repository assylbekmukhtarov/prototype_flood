"""
Визуализирует результаты анализа паводка.
Сохраняет PNG с 6 панелями: B3/B11 до и после, маски воды, новое затопление.

Запуск:
    venv/Scripts/python.exe tests/visualize_flood.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from sentinel import load_bands
from analysis import compute_indices, compute_masks, compute_new_flood, compute_area_km2

# ── Параметры (меняй здесь) ───────────────────────────────────────────────────
BBOX        = [51.8, 47.0, 52.8, 47.6]   # Атырау, Казахстан
DATE_BEFORE = ("2024-07-01", "2024-08-31")
DATE_AFTER  = ("2025-04-10", "2025-05-10")
WATER_THRESHOLD = 0.0
OUTPUT_FILE = "tests/flood_visualization.png"
# ─────────────────────────────────────────────────────────────────────────────

print("Загрузка снимка ДО...")
before = load_bands(BBOX, *DATE_BEFORE)
print(f"  -> {before['date']} (облачность {before['cloud_cover']:.1f}%)")

print("Загрузка снимка ПОСЛЕ...")
after = load_bands(BBOX, *DATE_AFTER)
print(f"  -> {after['date']} (облачность {after['cloud_cover']:.1f}%)")

# индексы и маски
_, mndwi_b = compute_indices(before["b3"], before["b11"])
_, water_before = compute_masks(mndwi_b, mndwi_b, water_threshold=WATER_THRESHOLD)

_, mndwi_a = compute_indices(after["b3"], after["b11"])
_, water_after = compute_masks(mndwi_a, mndwi_a, water_threshold=WATER_THRESHOLD)

# приводит к одному размеру
if water_before.shape != water_after.shape:
    from scipy.ndimage import zoom
    zy = water_after.shape[0] / water_before.shape[0]
    zx = water_after.shape[1] / water_before.shape[1]
    water_before = (zoom(water_before.astype(float), (zy, zx), order=0) > 0.5).astype(np.uint8)
    mndwi_b = zoom(mndwi_b, (zy, zx), order=1).astype(np.float32)

flood_mask = compute_new_flood(water_before, water_after)

area_before = compute_area_km2(water_before, before["real_bbox"])
area_after  = compute_area_km2(water_after,  after["real_bbox"])
flood_area  = compute_area_km2(flood_mask,   after["real_bbox"])

print(f"\nВода до:          {area_before:.1f} km2")
print(f"Вода после:       {area_after:.1f} km2")
print(f"Новое затопление: {flood_area:.1f} km2")

# ── Построение графика ────────────────────────────────────────────────────────
fig, axes = plt.subplots(2, 3, figsize=(15, 9))
fig.suptitle(
    f"Анализ паводка | bbox: {BBOX}\n"
    f"До: {before['date']}  |  После: {after['date']}",
    fontsize=13, fontweight="bold"
)

def norm(arr):
    """Нормализует массив в [0, 1] для отображения на графике."""
    mn, mx = np.percentile(arr, 2), np.percentile(arr, 98)
    return np.clip((arr - mn) / (mx - mn + 1e-6), 0, 1)

# строка 1: отображает исходные каналы
axes[0, 0].imshow(norm(before["b3"]), cmap="gray")
axes[0, 0].set_title(f"B3 (зелёный) ДО\n{before['date']}")

axes[0, 1].imshow(norm(after["b3"]), cmap="gray")
axes[0, 1].set_title(f"B3 (зелёный) ПОСЛЕ\n{after['date']}")

# MNDWI — синий = вода (высокие значения), отображает цветовой градиент RdBu
im = axes[0, 2].imshow(mndwi_a, cmap="RdBu", vmin=-0.5, vmax=0.5)
axes[0, 2].set_title("MNDWI ПОСЛЕ\n(синий = вода > 0)")
plt.colorbar(im, ax=axes[0, 2], fraction=0.046)

# строка 2: отображает маски воды
axes[1, 0].imshow(water_before, cmap="Blues", vmin=0, vmax=1)
axes[1, 0].set_title(f"Маска воды ДО\n{area_before:.1f} km2")

axes[1, 1].imshow(water_after, cmap="Blues", vmin=0, vmax=1)
axes[1, 1].set_title(f"Маска воды ПОСЛЕ\n{area_after:.1f} km2")

# новое затопление: накладывает синий=вода_до, красный=новое затопление
overlay = np.zeros((*flood_mask.shape, 3), dtype=np.float32)
overlay[water_before == 1] = [0.2, 0.5, 0.9]   # синий — старая вода
overlay[flood_mask   == 1] = [0.9, 0.2, 0.2]   # красный — новое затопление

axes[1, 2].imshow(overlay)
axes[1, 2].set_title(f"Новое затопление\n{flood_area:.1f} km2")

patch_old   = mpatches.Patch(color=(0.2, 0.5, 0.9), label=f"Вода до ({area_before:.1f} km2)")
patch_flood = mpatches.Patch(color=(0.9, 0.2, 0.2), label=f"Новое ({flood_area:.1f} km2)")
axes[1, 2].legend(handles=[patch_old, patch_flood], loc="lower right", fontsize=8)

for ax in axes.flat:
    ax.axis("off")

plt.tight_layout()
plt.savefig(OUTPUT_FILE, dpi=150, bbox_inches="tight")
print(f"\nСохранено: {OUTPUT_FILE}")
plt.show()
