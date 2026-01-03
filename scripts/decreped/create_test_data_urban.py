#!/usr/bin/env python3
"""
Test: Crear datos de prueba para validar el workflow de crop urbano
"""

import os
import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon
import rasterio
from rasterio.transform import from_bounds
import numpy as np

print("Creando datos de prueba para crop urbano...")

# Crear directorio de prueba
os.makedirs('test_urban_crop', exist_ok=True)

# 1. Crear AOI de prueba (cuadrado de 5km)
print("\n1. Creando AOI de prueba...")
aoi_bounds = (500000, 4680000, 505000, 4685000)  # EPSG:25831
aoi_geom = Polygon([
    (500000, 4680000),
    (505000, 4680000),
    (505000, 4685000),
    (500000, 4685000),
    (500000, 4680000)
])
aoi_gdf = gpd.GeoDataFrame({'name': ['test_aoi']}, geometry=[aoi_geom], crs='EPSG:25831')
aoi_gdf.to_file('test_urban_crop/aoi_test.geojson', driver='GeoJSON')
print("   ✓ test_urban_crop/aoi_test.geojson")

# 2. Crear MCC sintético con áreas urbanas y no urbanas
print("\n2. Creando MCC sintético...")
urban_polygons = [
    # Zona urbana consolidada (1110)
    Polygon([(500500, 4680500), (502000, 4680500), (502000, 4682000), (500500, 4682000)]),
    # Zona industrial (1210)
    Polygon([(502500, 4681000), (503500, 4681000), (503500, 4682000), (502500, 4682000)]),
    # Zona verde urbana (1410)
    Polygon([(501000, 4683000), (502000, 4683000), (502000, 4684000), (501000, 4684000)]),
]

rural_polygons = [
    # Zona agrícola (2xxx)
    Polygon([(503500, 4680500), (504500, 4680500), (504500, 4681500), (503500, 4681500)]),
    # Bosque (3xxx)
    Polygon([(500500, 4683500), (501500, 4683500), (501500, 4684500), (500500, 4684500)]),
]

# Crear GeoDataFrame con códigos
all_polygons = urban_polygons + rural_polygons
codes = ['1110', '1210', '1410', '2100', '3100']
mcc_gdf = gpd.GeoDataFrame({
    'codi': codes,
    'geometry': all_polygons
}, crs='EPSG:25831')

mcc_gdf.to_file('test_urban_crop/mcc_test.geojson', driver='GeoJSON')
print("   ✓ test_urban_crop/mcc_test.geojson")
print(f"   - {len(urban_polygons)} polígonos urbanos")
print(f"   - {len(rural_polygons)} polígonos rurales")

# 3. Crear raster InSAR sintético
print("\n3. Creando raster InSAR sintético...")
width, height = 500, 500
transform = from_bounds(*aoi_bounds, width, height)

# Crear coherencia sintética (valores más altos en zonas urbanas)
coherence = np.random.rand(height, width) * 0.3 + 0.2  # Base 0.2-0.5

# Aumentar coherencia en zonas urbanas
y_indices, x_indices = np.mgrid[0:height, 0:width]
x_coords = aoi_bounds[0] + (x_indices / width) * (aoi_bounds[2] - aoi_bounds[0])
y_coords = aoi_bounds[3] - (y_indices / height) * (aoi_bounds[3] - aoi_bounds[1])

# Zona urbana 1 (alta coherencia)
mask1 = (x_coords >= 500500) & (x_coords <= 502000) & (y_coords >= 4680500) & (y_coords <= 4682000)
coherence[mask1] = np.random.rand(mask1.sum()) * 0.3 + 0.6  # 0.6-0.9

# Zona urbana 2 (media-alta coherencia)
mask2 = (x_coords >= 502500) & (x_coords <= 503500) & (y_coords >= 4681000) & (y_coords <= 4682000)
coherence[mask2] = np.random.rand(mask2.sum()) * 0.2 + 0.5  # 0.5-0.7

# Guardar raster
with rasterio.open(
    'test_urban_crop/coherence_test.tif',
    'w',
    driver='GTiff',
    height=height,
    width=width,
    count=1,
    dtype=coherence.dtype,
    crs='EPSG:25831',
    transform=transform,
    compress='lzw'
) as dst:
    dst.write(coherence, 1)

print("   ✓ test_urban_crop/coherence_test.tif")
print(f"   - Resolución: {width}x{height}")
print(f"   - Coherencia media: {coherence.mean():.3f}")

print("\n" + "="*60)
print("DATOS DE PRUEBA CREADOS")
print("="*60)
print("\nPrueba el workflow con:")
print("\n  # Extraer áreas urbanas")
print("  python scripts/extract_urban_from_mcc.py \\")
print("    test_urban_crop/mcc_test.geojson \\")
print("    test_urban_crop/aoi_test.geojson \\")
print("    test_urban_crop/urban_mask.geojson")
print("\n  # Crear directorio simulado")
print("  mkdir -p test_urban_crop/workspace")
print("  cp test_urban_crop/coherence_test.tif test_urban_crop/workspace/")
print("  cp test_urban_crop/aoi_test.geojson test_urban_crop/workspace/")
print("\n  # Recortar a suelo urbano")
print("  python scripts/crop_to_urban_soil.py \\")
print("    test_urban_crop/workspace \\")
print("    --mcc-file test_urban_crop/urban_mask.geojson")
print("\n  # Ver resultado")
print("  ls -lh test_urban_crop/workspace/urban_products/")
print()
