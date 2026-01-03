#!/usr/bin/env python3
"""
Script para verificar que el AOI se aplicó correctamente en los productos
"""
import sys
import json
from pathlib import Path
from osgeo import gdal

def check_aoi(project_dir):
    """Verifica el AOI en un proyecto"""
    project_path = Path(project_dir)

    print(f"\n{'='*80}")
    print(f"VERIFICACIÓN DE AOI: {project_path.name}")
    print(f"{'='*80}\n")

    # 1. AOI Original
    aoi_file = project_path / "aoi.geojson"
    if aoi_file.exists():
        with open(aoi_file) as f:
            aoi_data = json.load(f)
        coords = aoi_data['features'][0]['geometry']['coordinates'][0]
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]

        print("1. AOI Original (aoi.geojson):")
        print(f"   Lon: {min(lons):.6f} a {max(lons):.6f}")
        print(f"   Lat: {min(lats):.6f} a {max(lats):.6f}")
        print(f"   Tamaño: {max(lons)-min(lons):.6f}° x {max(lats)-min(lats):.6f}°")
        print()

        aoi_original = {
            'min_lon': min(lons), 'max_lon': max(lons),
            'min_lat': min(lats), 'max_lat': max(lats)
        }
    else:
        print("❌ No se encontró aoi.geojson")
        return

    # 2. AOI en configuraciones
    config_files = list(project_path.glob("selected_products_*.json"))
    if config_files:
        print(f"2. AOI en configuraciones ({len(config_files)} archivos):")
        for config_file in sorted(config_files)[:3]:  # Mostrar solo los primeros 3
            with open(config_file) as f:
                config_data = json.load(f)
            bbox = config_data.get('aoi_bbox', {})
            if bbox:
                match = (
                    abs(bbox['min_lon'] - aoi_original['min_lon']) < 0.001 and
                    abs(bbox['max_lon'] - aoi_original['max_lon']) < 0.001 and
                    abs(bbox['min_lat'] - aoi_original['min_lat']) < 0.001 and
                    abs(bbox['max_lat'] - aoi_original['max_lat']) < 0.001
                )
                status = "✅" if match else "❌"
                print(f"   {status} {config_file.name}")
                if not match:
                    print(f"      Config: ({bbox['min_lon']:.6f}, {bbox['min_lat']:.6f}) a ({bbox['max_lon']:.6f}, {bbox['max_lat']:.6f})")
        print()

    # 3. Productos finales
    result_files = list(project_path.glob("insar_*/fusion/leak_probability_map.tif"))
    if result_files:
        print(f"3. Productos finales ({len(result_files)} series):")
        for result_file in sorted(result_files):
            series_name = result_file.parent.parent.name

            # Leer extensión del GeoTIFF
            ds = gdal.Open(str(result_file))
            if ds:
                gt = ds.GetGeoTransform()
                width = ds.RasterXSize
                height = ds.RasterYSize

                # Calcular extensión
                min_x = gt[0]
                max_x = gt[0] + width * gt[1]
                max_y = gt[3]
                min_y = gt[3] + height * gt[5]

                # Verificar si coincide con AOI original
                match = (
                    abs(min_x - aoi_original['min_lon']) < 0.01 and
                    abs(max_x - aoi_original['max_lon']) < 0.01 and
                    abs(min_y - aoi_original['min_lat']) < 0.01 and
                    abs(max_y - aoi_original['max_lat']) < 0.01
                )

                status = "✅" if match else "❌"
                print(f"   {status} {series_name}")
                print(f"      Extensión: ({min_x:.6f}, {min_y:.6f}) a ({max_x:.6f}, {max_y:.6f})")
                print(f"      Tamaño: {width} x {height} píxeles")

                if not match:
                    print(f"      ⚠️  Diferencia con AOI original:")
                    print(f"         Lon: {abs(min_x - aoi_original['min_lon']):.6f}° / {abs(max_x - aoi_original['max_lon']):.6f}°")
                    print(f"         Lat: {abs(min_y - aoi_original['min_lat']):.6f}° / {abs(max_y - aoi_original['max_lat']):.6f}°")

                ds = None
            else:
                print(f"   ❌ {series_name} - No se pudo leer")
            print()
    else:
        print("3. ⏳ Productos finales aún no generados\n")

    print(f"{'='*80}\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python verify_aoi.py processing/aoi_name")
        sys.exit(1)

    check_aoi(sys.argv[1])
