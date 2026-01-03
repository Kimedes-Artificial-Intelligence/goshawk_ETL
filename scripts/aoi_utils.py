#!/usr/bin/env python3
"""
Utilidades para manejo de AOI (Area of Interest)
Convierte GeoJSON a diferentes formatos usados en procesamiento SAR
"""

import json
import sys
from pathlib import Path
from typing import Dict, Tuple


def load_geojson(geojson_path: str) -> Dict:
    """Carga un archivo GeoJSON"""
    with open(geojson_path, 'r') as f:
        return json.load(f)


def extract_bbox_from_geojson(geojson: Dict) -> Dict[str, float]:
    """
    Extrae bounding box de un GeoJSON
    Soporta Polygon y MultiPolygon

    Returns:
        dict: {'min_lon': float, 'min_lat': float, 'max_lon': float, 'max_lat': float}
    """
    try:
        # Obtener geometría del primer feature
        geometry = geojson['features'][0]['geometry']
        geom_type = geometry['type']
        
        # Extraer coordenadas según el tipo
        if geom_type == 'Polygon':
            coords = geometry['coordinates'][0]  # Exterior ring
        elif geom_type == 'MultiPolygon':
            # Para MultiPolygon, obtener todos los puntos de todos los polígonos
            coords = []
            for polygon in geometry['coordinates']:
                coords.extend(polygon[0])  # Exterior ring de cada polígono
        else:
            raise ValueError(f"Tipo de geometría no soportado: {geom_type}")

        # Extraer lon/lat
        lons = [coord[0] for coord in coords]
        lats = [coord[1] for coord in coords]

        return {
            'min_lon': min(lons),
            'min_lat': min(lats),
            'max_lon': max(lons),
            'max_lat': max(lats)
        }
    except (KeyError, IndexError) as e:
        raise ValueError(f"GeoJSON inválido: {e}")


def bbox_to_wkt(bbox: Dict[str, float]) -> str:
    """
    Convierte bounding box a formato WKT (Well-Known Text) POLYGON

    Args:
        bbox: dict con min_lon, min_lat, max_lon, max_lat

    Returns:
        str: POLYGON en formato WKT
    """
    return (
        f"POLYGON(("
        f"{bbox['min_lon']} {bbox['min_lat']}, "
        f"{bbox['max_lon']} {bbox['min_lat']}, "
        f"{bbox['max_lon']} {bbox['max_lat']}, "
        f"{bbox['min_lon']} {bbox['max_lat']}, "
        f"{bbox['min_lon']} {bbox['min_lat']}"
        f"))"
    )


def geojson_to_wkt(geojson_path: str) -> str:
    """
    Convierte GeoJSON directamente a WKT

    Args:
        geojson_path: Ruta al archivo GeoJSON

    Returns:
        str: POLYGON en formato WKT
    """
    geojson = load_geojson(geojson_path)
    bbox = extract_bbox_from_geojson(geojson)
    return bbox_to_wkt(bbox)


def geojson_to_bbox(geojson_path: str) -> Dict[str, float]:
    """
    Extrae bounding box desde archivo GeoJSON

    Args:
        geojson_path: Ruta al archivo GeoJSON

    Returns:
        dict: {'min_lon': float, 'min_lat': float, 'max_lon': float, 'max_lat': float}
    """
    geojson = load_geojson(geojson_path)
    return extract_bbox_from_geojson(geojson)


def calculate_bbox_dimensions(bbox: Dict[str, float]) -> Tuple[float, float]:
    """
    Calcula dimensiones del bounding box en grados

    Returns:
        tuple: (width_degrees, height_degrees)
    """
    width = bbox['max_lon'] - bbox['min_lon']
    height = bbox['max_lat'] - bbox['min_lat']
    return (width, height)


def calculate_bbox_area_km2(bbox: Dict[str, float]) -> float:
    """
    Estima área del bounding box en km²

    Aproximación: 1° ≈ 111 km (varía con latitud)

    Returns:
        float: Área aproximada en km²
    """
    width, height = calculate_bbox_dimensions(bbox)

    # Ajustar por latitud (1° lon varía con latitud)
    avg_lat = (bbox['min_lat'] + bbox['max_lat']) / 2
    import math
    lon_km_per_degree = 111.32 * math.cos(math.radians(avg_lat))
    lat_km_per_degree = 111.32

    width_km = width * lon_km_per_degree
    height_km = height * lat_km_per_degree

    return width_km * height_km


def format_bbox_for_config(bbox: Dict[str, float]) -> str:
    """
    Formatea bounding box para archivo config.txt

    Returns:
        str: Línea para config.txt con formato AOI="POLYGON(...)"
    """
    wkt = bbox_to_wkt(bbox)
    return f'AOI="{wkt}"'


def validate_aoi_size(bbox: Dict[str, float], min_degrees: float = 0.01, max_degrees: float = 2.0) -> Tuple[bool, str]:
    """
    Valida que el AOI tenga dimensiones razonables

    Args:
        bbox: Bounding box a validar
        min_degrees: Dimensión mínima en grados (default: 0.01° ≈ 1km)
        max_degrees: Dimensión máxima en grados (default: 2.0° ≈ 200km)

    Returns:
        tuple: (is_valid: bool, message: str)
    """
    width, height = calculate_bbox_dimensions(bbox)

    if width < min_degrees or height < min_degrees:
        return False, f"AOI muy pequeño ({width:.4f}° x {height:.4f}°). Mínimo: {min_degrees}°"

    if width > max_degrees or height > max_degrees:
        return False, f"AOI muy grande ({width:.4f}° x {height:.4f}°). Máximo: {max_degrees}°"

    area_km2 = calculate_bbox_area_km2(bbox)
    return True, f"AOI válido: {width:.4f}° x {height:.4f}° (~{area_km2:.1f} km²)"


def print_aoi_info(geojson_path: str):
    """Imprime información completa del AOI"""
    try:
        # Cargar y parsear
        geojson = load_geojson(geojson_path)
        bbox = extract_bbox_from_geojson(geojson)
        wkt = bbox_to_wkt(bbox)

        # Información básica
        print("="*70)
        print("INFORMACIÓN DEL AOI")
        print("="*70)
        print(f"\nArchivo: {geojson_path}")

        # Metadata del GeoJSON
        try:
            name = geojson['features'][0]['properties'].get('name', 'N/A')
            area_desc = geojson['features'][0]['properties'].get('area', 'N/A')
            print(f"Nombre: {name}")
            print(f"Descripción: {area_desc}")
        except (KeyError, IndexError):
            pass

        # Bounding box
        print(f"\nBounding Box:")
        print(f"  Lon: {bbox['min_lon']:.6f} → {bbox['max_lon']:.6f}")
        print(f"  Lat: {bbox['min_lat']:.6f} → {bbox['max_lat']:.6f}")

        # Dimensiones
        width, height = calculate_bbox_dimensions(bbox)
        print(f"\nDimensiones:")
        print(f"  Ancho: {width:.6f}° (~{width * 111:.1f} km)")
        print(f"  Alto: {height:.6f}° (~{height * 111:.1f} km)")

        # Área
        area_km2 = calculate_bbox_area_km2(bbox)
        print(f"  Área: ~{area_km2:.1f} km²")

        # Validación
        is_valid, message = validate_aoi_size(bbox)
        status = "✅" if is_valid else "❌"
        print(f"\nValidación: {status} {message}")

        # Formatos de salida
        print(f"\n{'='*70}")
        print("FORMATOS DE SALIDA")
        print("="*70)

        print(f"\nWKT (para SNAP/config.txt):")
        print(f'  {wkt}')

        print(f"\nBBOX Python dict (para scripts):")
        print(f"  BBOX = {{")
        print(f"      'min_lon': {bbox['min_lon']},")
        print(f"      'min_lat': {bbox['min_lat']},")
        print(f"      'max_lon': {bbox['max_lon']},")
        print(f"      'max_lat': {bbox['max_lat']}")
        print(f"  }}")

        print(f"\nConfig.txt:")
        print(f"  {format_bbox_for_config(bbox)}")

        print()

    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    """CLI para utilidades de AOI"""
    import argparse

    parser = argparse.ArgumentParser(
        description='Utilidades para convertir AOI GeoJSON',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  # Ver información completa del AOI
  python3 scripts/aoi_utils.py aoi_arenys.geojson

  # Convertir a WKT
  python3 scripts/aoi_utils.py aoi_arenys.geojson --wkt

  # Extraer BBOX
  python3 scripts/aoi_utils.py aoi_arenys.geojson --bbox

  # Generar línea para config.txt
  python3 scripts/aoi_utils.py aoi_arenys.geojson --config
        """
    )

    parser.add_argument('geojson', help='Archivo GeoJSON con el AOI')
    parser.add_argument('--wkt', action='store_true', help='Solo mostrar WKT')
    parser.add_argument('--bbox', action='store_true', help='Solo mostrar BBOX')
    parser.add_argument('--config', action='store_true', help='Generar línea para config.txt')
    parser.add_argument('--validate', action='store_true', help='Solo validar AOI')

    args = parser.parse_args()

    if not Path(args.geojson).exists():
        print(f"❌ Error: No existe el archivo {args.geojson}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.wkt:
            print(geojson_to_wkt(args.geojson))
        elif args.bbox:
            bbox = geojson_to_bbox(args.geojson)
            print(json.dumps(bbox, indent=2))
        elif args.config:
            bbox = geojson_to_bbox(args.geojson)
            print(format_bbox_for_config(bbox))
        elif args.validate:
            bbox = geojson_to_bbox(args.geojson)
            is_valid, message = validate_aoi_size(bbox)
            status = "✅" if is_valid else "❌"
            print(f"{status} {message}")
            sys.exit(0 if is_valid else 1)
        else:
            print_aoi_info(args.geojson)

    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
