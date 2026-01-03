#!/usr/bin/env python3
"""
Script: analyze_burst_coverage.py
Descripción: Analiza cuántos bursts cubren el AOI y muestra geometría de cobertura

Uso:
    python scripts/analyze_burst_coverage.py
    python scripts/analyze_burst_coverage.py --metadata data/sentinel1_slc/metadata_slc.json
    python scripts/analyze_burst_coverage.py --data-dir data/sentinel1_slc
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Dict, Optional

# Añadir el directorio raíz al path para imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_aoi_geojson(geojson_path: str = "aoi_arenys.geojson") -> Optional[Dict]:
    """Carga el AOI desde archivo GeoJSON"""
    try:
        with open(geojson_path, 'r') as f:
            data = json.load(f)
            coords = data['features'][0]['geometry']['coordinates'][0]

            # Extraer bbox
            lons = [c[0] for c in coords]
            lats = [c[1] for c in coords]

            return {
                'min_lon': min(lons),
                'max_lon': max(lons),
                'min_lat': min(lats),
                'max_lat': max(lats),
                'center_lon': (min(lons) + max(lons)) / 2,
                'center_lat': (min(lats) + max(lats)) / 2,
                'width_deg': max(lons) - min(lons),
                'height_deg': max(lats) - min(lats)
            }
    except Exception as e:
        print(f"⚠️  Error cargando {geojson_path}: {e}")
        return None


def parse_s1_filename(filename: str) -> Optional[Dict]:
    """
    Parsea nombre de producto Sentinel-1 para extraer información

    Ejemplo:
    S1C_IW_SLC__1SDV_20250922T055159_20250922T055227_061172_07A0B4_4482.SAFE

    Returns:
        {
            'satellite': 'S1C',
            'mode': 'IW',
            'product_type': 'SLC',
            'start_time': datetime,
            'end_time': datetime,
            'date_str': '2025-09-22',
            'time_str': '05:51:59'
        }
    """
    try:
        clean_name = filename.replace('.SAFE', '')
        parts = clean_name.split('_')

        if not filename.startswith('S1'):
            return None

        satellite = parts[0]  # S1A, S1B, S1C
        mode = parts[1]       # IW
        product_type = parts[2]  # SLC, GRD

        # Buscar fechas (formato: 20YYMMDDTHHMMSS)
        start_time = None
        end_time = None

        for i, part in enumerate(parts):
            if len(part) >= 15 and 'T' in part and part[:4].isdigit():
                if start_time is None:
                    start_time = datetime.strptime(part[:15], '%Y%m%dT%H%M%S')
                else:
                    end_time = datetime.strptime(part[:15], '%Y%m%dT%H%M%S')
                    break

        if not start_time:
            return None

        return {
            'satellite': satellite,
            'mode': mode,
            'product_type': product_type,
            'start_time': start_time,
            'end_time': end_time,
            'date_str': start_time.strftime('%Y-%m-%d'),
            'time_str': start_time.strftime('%H:%M:%S'),
            'duration_sec': (end_time - start_time).total_seconds() if end_time else 0
        }
    except Exception as e:
        return None


def analyze_burst_coverage_from_metadata(metadata_path: str) -> Dict:
    """Analiza cobertura de bursts desde archivo metadata.json"""

    try:
        with open(metadata_path, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ Error leyendo metadata: {e}")
        return {}

    products = data.get('products', [])

    if not products:
        print("❌ No se encontraron productos en metadata")
        return {}

    # Agrupar por fecha
    by_date = defaultdict(list)

    for prod in products:
        name = prod.get('Name', '')
        info = parse_s1_filename(name)

        if not info:
            continue

        date = info['date_str']
        by_date[date].append({
            'name': name,
            'info': info,
            'size_gb': prod.get('ContentLength', 0) / (1024**3)
        })

    return by_date


def analyze_burst_coverage_from_dir(data_dir: str) -> Dict:
    """Analiza cobertura de bursts desde directorio de datos"""

    if not os.path.exists(data_dir):
        print(f"❌ Directorio no existe: {data_dir}")
        return {}

    # Buscar archivos .SAFE
    safe_dirs = []
    for item in os.listdir(data_dir):
        if item.endswith('.SAFE'):
            safe_dirs.append(item)

    if not safe_dirs:
        print(f"❌ No se encontraron productos .SAFE en {data_dir}")
        return {}

    # Agrupar por fecha
    by_date = defaultdict(list)

    for safe_dir in safe_dirs:
        info = parse_s1_filename(safe_dir)

        if not info:
            continue

        date = info['date_str']

        # Calcular tamaño
        full_path = os.path.join(data_dir, safe_dir)
        size_bytes = 0
        try:
            for root, dirs, files in os.walk(full_path):
                for f in files:
                    fp = os.path.join(root, f)
                    if os.path.exists(fp):
                        size_bytes += os.path.getsize(fp)
        except Exception:
            pass

        by_date[date].append({
            'name': safe_dir,
            'info': info,
            'size_gb': size_bytes / (1024**3)
        })

    return by_date


def print_coverage_analysis(by_date: Dict, aoi: Optional[Dict] = None, product_type: str = 'SLC'):
    """Imprime análisis de cobertura de bursts"""

    print("\n" + "="*80)
    print(f"ANÁLISIS DE COBERTURA DE BURSTS {product_type}")
    print("="*80)

    if aoi:
        print(f"\n📍 AOI:")
        print(f"   Centro: {aoi['center_lon']:.4f}°, {aoi['center_lat']:.4f}°")
        print(f"   Dimensiones: {aoi['width_deg']*111:.1f} km × {aoi['height_deg']*111:.1f} km")
        print(f"   (1° ≈ 111 km en latitud, ≈{111*abs(aoi['center_lat'])/90:.0f} km en longitud a {aoi['center_lat']:.1f}°)")

    print(f"\n📡 Productos encontrados:")

    if not by_date:
        print("   ⚠️  No se encontraron productos")
        return

    # Estadísticas
    dates_with_multiple_bursts = 0
    total_bursts = 0
    max_bursts_per_date = 0

    # Ordenar por fecha
    sorted_dates = sorted(by_date.keys())

    print(f"\n{'Fecha':<12} {'Bursts':<8} {'Inicio':<10} {'Fin':<10} {'Δt(s)':<8} {'Tamaño(GB)':<12}")
    print("-"*80)

    for date in sorted_dates:
        bursts = by_date[date]
        num_bursts = len(bursts)
        total_bursts += num_bursts

        if num_bursts > 1:
            dates_with_multiple_bursts += 1
            max_bursts_per_date = max(max_bursts_per_date, num_bursts)

        # Ordenar bursts por hora
        bursts.sort(key=lambda x: x['info']['start_time'])

        for i, burst in enumerate(bursts):
            info = burst['info']

            # Calcular overlap con siguiente burst
            overlap_str = ""
            if i < len(bursts) - 1:
                next_burst = bursts[i + 1]
                time_gap = (next_burst['info']['start_time'] - info['end_time']).total_seconds() if info['end_time'] else 0

                if time_gap < 0:
                    overlap_str = f" ⚠️ overlap {abs(time_gap):.0f}s"
                elif time_gap < 10:
                    overlap_str = f" ✓ consecutivo"

            date_str = date if i == 0 else ""
            num_str = f"{num_bursts}" if i == 0 else ""

            start_time = info['time_str']
            end_time = info['end_time'].strftime('%H:%M:%S') if info['end_time'] else "N/A"
            duration = info['duration_sec']
            size = burst['size_gb']

            print(f"{date_str:<12} {num_str:<8} {start_time:<10} {end_time:<10} {duration:<8.0f} {size:<12.2f}{overlap_str}")

    print("-"*80)
    print(f"Total: {len(sorted_dates)} fechas, {total_bursts} bursts")

    # Resumen
    print(f"\n📊 Resumen:")
    print(f"   Fechas únicas: {len(sorted_dates)}")
    print(f"   Fechas con múltiples bursts: {dates_with_multiple_bursts}")
    print(f"   Máximo bursts por fecha: {max_bursts_per_date}")

    if dates_with_multiple_bursts > 0:
        print(f"\n💡 Interpretación:")
        print(f"   ✓ {dates_with_multiple_bursts} fechas tienen múltiples bursts")
        print(f"   → Tu AOI probablemente está en la frontera entre bursts")
        print(f"   → Esto garantiza cobertura completa del área")

        # Análisis de dimensiones
        if aoi:
            aoi_size_km = aoi['height_deg'] * 111  # Azimut (along-track)
            burst_size_km = 25  # Tamaño típico de burst

            if aoi_size_km > burst_size_km * 0.8:
                print(f"\n⚠️  Tu AOI ({aoi_size_km:.1f} km) es casi tan grande como un burst ({burst_size_km} km)")
                print(f"   → Es normal necesitar múltiples bursts para cobertura completa")
            else:
                print(f"\n📏 Tu AOI ({aoi_size_km:.1f} km) es más pequeño que un burst ({burst_size_km} km)")
                print(f"   → Los múltiples bursts indican que el AOI está en la frontera")
    else:
        print(f"\n✓ Cada fecha tiene un solo burst → AOI completamente dentro de bursts individuales")

    # Mensaje específico según tipo de producto
    if product_type == 'SLC':
        print(f"\n🔧 Procesamiento InSAR:")
        print(f"   ✓ Fusionar bursts del mismo día: python scripts/merge_same_day_bursts.py")
        print(f"   ✓ Procesar InSAR: python scripts/process_insar_gpt.py --use-merged")
        print(f"   → Garantiza cobertura completa del AOI en interferogramas")
    else:  # GRD
        print(f"\n🔧 Procesamiento SAR:")
        print(f"   ✓ Fusionar bursts del mismo día: python scripts/merge_same_day_bursts.py --product-type GRD")
        print(f"   ✓ Procesar GRD: python scripts/process_sar_gpt.py --use-merged")
        print(f"   → Evita sobrescribir datos y garantiza cobertura completa del AOI")


def main():
    parser = argparse.ArgumentParser(
        description='Analiza cobertura de bursts sobre el AOI',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument('--product-type', '-t',
                       choices=['SLC', 'GRD'],
                       default='SLC',
                       help='Tipo de producto (default: SLC)')
    parser.add_argument('--metadata', '-m',
                       help='Archivo metadata_{slc|grd}.json')
    parser.add_argument('--data-dir', '-d',
                       help='Directorio con productos .SAFE')
    parser.add_argument('--aoi', default='aoi_arenys.geojson',
                       help='Archivo GeoJSON con AOI (default: aoi_arenys.geojson)')

    args = parser.parse_args()

    # Cargar AOI
    aoi = load_aoi_geojson(args.aoi)

    # Analizar cobertura
    by_date = {}

    if args.metadata:
        print(f"📖 Analizando desde metadata: {args.metadata}")
        by_date = analyze_burst_coverage_from_metadata(args.metadata)
    elif args.data_dir:
        print(f"📂 Analizando desde directorio: {args.data_dir}")
        by_date = analyze_burst_coverage_from_dir(args.data_dir)
    else:
        # Intentar encontrar automáticamente según tipo de producto
        product_type_lower = args.product_type.lower()
        possible_paths = [
            f'data/sentinel1_{product_type_lower}/metadata_{product_type_lower}.json',
            f'data/sentinel1_{product_type_lower}',
        ]

        for path in possible_paths:
            if os.path.exists(path):
                if path.endswith('.json'):
                    print(f"📖 Detectado metadata: {path}")
                    by_date = analyze_burst_coverage_from_metadata(path)
                else:
                    print(f"📂 Detectado directorio: {path}")
                    by_date = analyze_burst_coverage_from_dir(path)
                break

        if not by_date:
            print(f"\n⚠️  No se encontraron datos {args.product_type} automáticamente")
            print("\nUso:")
            print(f"  python scripts/analyze_burst_coverage.py --product-type {args.product_type} --metadata data/sentinel1_{product_type_lower}/metadata_{product_type_lower}.json")
            print(f"  python scripts/analyze_burst_coverage.py --product-type {args.product_type} --data-dir data/sentinel1_{product_type_lower}")
            return 1

    # Imprimir análisis
    print_coverage_analysis(by_date, aoi, product_type=args.product_type)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrumpido")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
