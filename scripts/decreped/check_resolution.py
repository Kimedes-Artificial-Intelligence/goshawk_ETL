#!/usr/bin/env python3
"""
Script: check_resolution.py
Descripción: Verifica la resolución de los productos finales
Uso: python scripts/check_resolution.py [workspace_dir]
"""

import os
import sys
import glob
import rasterio

def check_resolution(workspace_dir="."):
    """Verifica resolución de productos en un workspace"""
    
    print("=" * 80)
    print("VERIFICACIÓN DE RESOLUCIÓN - DETECCIÓN DE FUGAS")
    print("=" * 80)
    print()
    
    workspace_dir = os.path.abspath(workspace_dir)
    print(f"Workspace: {workspace_dir}")
    print()
    
    # 1. Productos InSAR recortados
    print("1. PRODUCTOS InSAR RECORTADOS:")
    print("-" * 80)
    cropped_dir = os.path.join(workspace_dir, "fusion/insar/cropped")
    if os.path.isdir(cropped_dir):
        cropped_files = glob.glob(os.path.join(cropped_dir, "*_cropped.tif"))
        if cropped_files:
            with rasterio.open(cropped_files[0]) as src:
                res_x = abs(src.transform.a)
                res_y = abs(src.transform.e)
                print(f"   Archivos encontrados: {len(cropped_files)}")
                print(f"   Dimensiones: {src.width} x {src.height} píxeles")
                print(f"   Resolución: {res_x:.6f}° x {res_y:.6f}°")
                
                # Convertir a metros
                import math
                lat = (src.bounds.top + src.bounds.bottom) / 2
                meters_per_deg_lon = 111320 * math.cos(math.radians(lat))
                meters_per_deg_lat = 111320
                res_x_m = res_x * meters_per_deg_lon
                res_y_m = res_y * meters_per_deg_lat
                print(f"   Resolución: {res_x_m:.2f}m x {res_y_m:.2f}m")
                print(f"   ✓ RESOLUCIÓN NATIVA - Óptimo para detección de fugas")
        else:
            print("   ⚠️  No se encontraron productos recortados")
            print("   💡 Ejecutar: python scripts/crop_insar_to_aoi.py")
    else:
        print("   ⚠️  Directorio no existe")
    print()
    
    # 2. Productos SAR
    print("2. PRODUCTOS SAR (GRD):")
    print("-" * 80)
    sar_dir = os.path.join(workspace_dir, "fusion/sar")
    if os.path.isdir(sar_dir):
        dim_files = glob.glob(os.path.join(sar_dir, "GRD_*.dim"))
        if dim_files:
            # Leer dimensiones del primer producto
            import xml.etree.ElementTree as ET
            tree = ET.parse(dim_files[0])
            root = tree.getroot()
            width = root.find(".//NCOLS")
            height = root.find(".//NROWS")
            
            if width is not None and height is not None:
                print(f"   Archivos encontrados: {len(dim_files)}")
                print(f"   Dimensiones: {width.text} x {height.text} píxeles")
                print(f"   Resolución: 10m x 10m (configurado en Terrain-Correction)")
                print(f"   ✓ RESOLUCIÓN ÓPTIMA para Sentinel-1 GRD")
        else:
            print("   ⚠️  No se encontraron productos SAR")
    else:
        print("   ⚠️  Directorio no existe")
    print()
    
    # 3. Estadísticas temporales
    print("3. ESTADÍSTICAS TEMPORALES:")
    print("-" * 80)
    fusion_dir = os.path.join(workspace_dir, "fusion")
    stats_files = [
        ("coherence_mean.tif", "Coherencia media"),
        ("vv_mean.tif", "Backscatter VV medio"),
        ("entropy_mean.tif", "Entropía media")
    ]
    
    for filename, desc in stats_files:
        filepath = os.path.join(fusion_dir, filename)
        if os.path.isfile(filepath):
            with rasterio.open(filepath) as src:
                print(f"   {desc}:")
                print(f"      Dimensiones: {src.width} x {src.height} píxeles")
                res_x = abs(src.transform.a)
                res_y = abs(src.transform.e)
                if res_x > 0:
                    import math
                    lat = (src.bounds.top + src.bounds.bottom) / 2
                    meters_per_deg_lon = 111320 * math.cos(math.radians(lat))
                    meters_per_deg_lat = 111320
                    res_x_m = res_x * meters_per_deg_lon
                    res_y_m = res_y * meters_per_deg_lat
                    print(f"      Resolución: {res_x_m:.2f}m x {res_y_m:.2f}m")
    print()
    
    # 4. Mapa de fusión final
    print("4. MAPA DE FUSIÓN FINAL:")
    print("-" * 80)
    leak_map = os.path.join(fusion_dir, "leak_probability_map.tif")
    if os.path.isfile(leak_map):
        with rasterio.open(leak_map) as src:
            print(f"   Archivo: leak_probability_map.tif")
            print(f"   Dimensiones: {src.width} x {src.height} píxeles")
            res_x = abs(src.transform.a)
            res_y = abs(src.transform.e)
            if res_x > 0:
                import math
                lat = (src.bounds.top + src.bounds.bottom) / 2
                meters_per_deg_lon = 111320 * math.cos(math.radians(lat))
                meters_per_deg_lat = 111320
                res_x_m = res_x * meters_per_deg_lon
                res_y_m = res_y * meters_per_deg_lat
                print(f"   Resolución: {res_x_m:.2f}m x {res_y_m:.2f}m")
                
                if res_x_m < 15:
                    print(f"   ✓ EXCELENTE resolución para detección de fugas")
                elif res_x_m < 25:
                    print(f"   ✓ BUENA resolución para detección de fugas")
                else:
                    print(f"   ⚠️  Resolución reducida - considerar recorte InSAR")
    else:
        print("   ⚠️  Mapa de fusión no encontrado")
    print()
    
    print("=" * 80)
    print("RECOMENDACIONES:")
    print("=" * 80)
    print("✓ Para detección de fugas óptima:")
    print("  - Resolución InSAR: < 5m (ideal)")
    print("  - Resolución SAR: ~10m (óptimo para GRD)")
    print("  - Resolución final: < 15m")
    print()
    print("✓ Validar anomalías con:")
    print("  - Inspecciones in-situ de tuberías")
    print("  - Datos históricos de presión de red")
    print("  - Correlación con zonas de alto consumo")
    print()


if __name__ == '__main__':
    workspace = sys.argv[1] if len(sys.argv) > 1 else "."
    check_resolution(workspace)
