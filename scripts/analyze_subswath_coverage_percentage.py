#!/usr/bin/env python3
"""
Script: analyze_subswath_coverage_percentage.py
Descripción: Analiza el porcentaje de cobertura del AOI por cada subswath
             usando los bounds exactos de los bursts

Uso:
    python scripts/analyze_subswath_coverage_percentage.py \
        --aoi aoi/argentona.geojson \
        --products processing/argentona/selected_products_desc_iw2.json
"""

import sys
import json
import argparse
from pathlib import Path
from typing import Dict, Tuple
import xml.etree.ElementTree as ET
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).parent))
from common_utils import Colors


def geojson_to_polygon(geojson_path: str) -> Polygon:
    """Convierte GeoJSON a polígono Shapely"""
    with open(geojson_path) as f:
        data = json.load(f)
    
    coords = data['features'][0]['geometry']['coordinates'][0]
    return Polygon(coords)


def extract_burst_geometry_from_product(product_path: Path, subswath: str) -> list:
    """
    Extrae la geometría de todos los bursts de un subswath específico
    
    Returns:
        list: Lista de polígonos (uno por burst)
    """
    bursts = []
    
    # Buscar archivos de anotación para el subswath
    annotation_dir = product_path / 'annotation'
    if not annotation_dir.exists():
        return bursts
    
    annotation_files = list(annotation_dir.glob(f's1?-{subswath.lower()}-slc-*.xml'))
    
    for ann_file in annotation_files:
        try:
            tree = ET.parse(ann_file)
            root = tree.getroot()
            
            # Buscar geolocationGrid
            geoloc_grid = root.find('.//geolocationGrid')
            if geoloc_grid is None:
                continue
            
            # Extraer puntos de grid
            grid_points = geoloc_grid.findall('.//geolocationGridPoint')
            if not grid_points:
                continue
            
            # Obtener coordenadas de las esquinas (simplificado)
            lons = []
            lats = []
            
            for point in grid_points:
                lon_elem = point.find('longitude')
                lat_elem = point.find('latitude')
                if lon_elem is not None and lat_elem is not None:
                    lons.append(float(lon_elem.text))
                    lats.append(float(lat_elem.text))
            
            if lons and lats:
                # Crear bounding box del burst
                min_lon, max_lon = min(lons), max(lons)
                min_lat, max_lat = min(lats), max(lats)
                
                burst_poly = box(min_lon, min_lat, max_lon, max_lat)
                bursts.append(burst_poly)
        
        except Exception as e:
            print(f"  Warning: No se pudo parsear {ann_file.name}: {e}")
            continue
    
    return bursts


def calculate_coverage_percentage(aoi_poly: Polygon, product_path: Path, subswath: str) -> Tuple[float, Polygon]:
    """
    Calcula el porcentaje del AOI cubierto por un subswath específico
    
    Returns:
        tuple: (percentage, intersection_polygon)
    """
    # Extraer geometría de bursts
    burst_polygons = extract_burst_geometry_from_product(product_path, subswath)
    
    if not burst_polygons:
        return (0.0, None)
    
    # Unir todos los bursts del subswath
    try:
        subswath_coverage = unary_union(burst_polygons)
    except Exception as e:
        print(f"  Error uniendo bursts: {e}")
        return (0.0, None)
    
    # Calcular intersección con AOI
    try:
        if not aoi_poly.intersects(subswath_coverage):
            return (0.0, None)
        
        intersection = aoi_poly.intersection(subswath_coverage)
        
        # Calcular porcentaje
        aoi_area = aoi_poly.area
        intersection_area = intersection.area
        
        percentage = (intersection_area / aoi_area) * 100.0 if aoi_area > 0 else 0.0
        
        return (percentage, intersection)
    
    except Exception as e:
        print(f"  Error calculando intersección: {e}")
        return (0.0, None)


def analyze_products_coverage(aoi_geojson: str, products_json: str):
    """Analiza la cobertura de productos seleccionados"""
    
    # Cargar AOI
    aoi_poly = geojson_to_polygon(aoi_geojson)
    aoi_area_km2 = aoi_poly.area * (111 * 111)  # Aproximación grados² a km²
    
    print(f"\n{Colors.CYAN}{Colors.BOLD}{'=' * 80}{Colors.NC}")
    print(f"{Colors.CYAN}{Colors.BOLD}ANÁLISIS DE COBERTURA POR SUBSWATH{Colors.NC}")
    print(f"{Colors.CYAN}{Colors.BOLD}{'=' * 80}{Colors.NC}\n")
    
    print(f"AOI: {Path(aoi_geojson).name}")
    print(f"Área AOI: {aoi_area_km2:.2f} km²")
    print(f"Bounds: {aoi_poly.bounds}")
    print()
    
    # Cargar productos seleccionados
    with open(products_json) as f:
        data = json.load(f)
    
    orbit = data['orbit_direction']
    subswath = data['subswath']
    products = data['products']
    
    print(f"Serie: {orbit} - {subswath}")
    print(f"Productos a analizar: {len(products)}")
    print()
    
    print(f"{Colors.YELLOW}{'=' * 80}{Colors.NC}")
    print(f"{Colors.YELLOW}ANÁLISIS DETALLADO DE COBERTURA{Colors.NC}")
    print(f"{Colors.YELLOW}{'=' * 80}{Colors.NC}\n")
    
    coverages = []
    
    for i, product_info in enumerate(products, 1):
        product_path = Path(product_info['path'])
        product_name = product_path.name
        date = product_info['date']
        
        print(f"[{i}/{len(products)}] {date} - {product_name}")
        
        if not product_path.exists():
            print(f"  {Colors.RED}✗ Producto no encontrado{Colors.NC}\n")
            continue
        
        # Analizar cobertura del subswath específico
        percentage, intersection_poly = calculate_coverage_percentage(aoi_poly, product_path, subswath)
        
        coverages.append({
            'date': date,
            'product': product_name,
            'coverage_pct': percentage,
            'has_intersection': intersection_poly is not None
        })
        
        if percentage > 0:
            color = Colors.GREEN if percentage >= 95 else Colors.YELLOW if percentage >= 50 else Colors.RED
            print(f"  {color}✓ Cobertura: {percentage:.2f}%{Colors.NC}")
            
            if intersection_poly:
                intersection_area_km2 = intersection_poly.area * (111 * 111)
                print(f"    Área intersección: {intersection_area_km2:.3f} km²")
        else:
            print(f"  {Colors.RED}✗ Sin cobertura (0.00%){Colors.NC}")
        
        print()
    
    # Resumen
    print(f"{Colors.CYAN}{'=' * 80}{Colors.NC}")
    print(f"{Colors.CYAN}RESUMEN{Colors.NC}")
    print(f"{Colors.CYAN}{'=' * 80}{Colors.NC}\n")
    
    products_with_coverage = [c for c in coverages if c['coverage_pct'] > 0]
    products_full_coverage = [c for c in coverages if c['coverage_pct'] >= 95]
    products_partial_coverage = [c for c in coverages if 0 < c['coverage_pct'] < 95]
    products_no_coverage = [c for c in coverages if c['coverage_pct'] == 0]
    
    print(f"Total productos: {len(coverages)}")
    print(f"  {Colors.GREEN}✓ Cobertura completa (≥95%): {len(products_full_coverage)}{Colors.NC}")
    print(f"  {Colors.YELLOW}⚠ Cobertura parcial (<95%): {len(products_partial_coverage)}{Colors.NC}")
    print(f"  {Colors.RED}✗ Sin cobertura (0%): {len(products_no_coverage)}{Colors.NC}")
    print()
    
    if products_with_coverage:
        avg_coverage = sum(c['coverage_pct'] for c in products_with_coverage) / len(products_with_coverage)
        min_coverage = min(c['coverage_pct'] for c in products_with_coverage)
        max_coverage = max(c['coverage_pct'] for c in products_with_coverage)
        
        print(f"Estadísticas de cobertura (productos con intersección):")
        print(f"  Promedio: {avg_coverage:.2f}%")
        print(f"  Mínimo: {min_coverage:.2f}%")
        print(f"  Máximo: {max_coverage:.2f}%")
        print()
    
    # Conclusión
    print(f"{Colors.BOLD}CONCLUSIÓN:{Colors.NC}")
    if len(products_full_coverage) == len(coverages):
        print(f"  {Colors.GREEN}✓ Todos los productos tienen cobertura completa{Colors.NC}")
        print(f"  {Colors.GREEN}  → Este subswath es APTO para procesamiento InSAR{Colors.NC}")
    elif len(products_no_coverage) == len(coverages):
        print(f"  {Colors.RED}✗ Ningún producto cubre el AOI{Colors.NC}")
        print(f"  {Colors.RED}  → Este subswath NO es apto para este AOI{Colors.NC}")
        print(f"  {Colors.RED}  → Usa otro subswath o cambia el AOI{Colors.NC}")
    else:
        print(f"  {Colors.YELLOW}⚠ Cobertura irregular entre productos{Colors.NC}")
        print(f"  {Colors.YELLOW}  → El procesamiento InSAR puede tener problemas{Colors.NC}")
        print(f"  {Colors.YELLOW}  → Considera filtrar productos con baja cobertura{Colors.NC}")
    
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Analiza el porcentaje de cobertura del AOI por subswath'
    )
    parser.add_argument('--aoi', required=True, help='Archivo GeoJSON del AOI')
    parser.add_argument('--products', required=True, help='JSON de productos seleccionados')
    
    args = parser.parse_args()
    
    if not Path(args.aoi).exists():
        print(f"Error: AOI no encontrado: {args.aoi}")
        return 1
    
    if not Path(args.products).exists():
        print(f"Error: JSON de productos no encontrado: {args.products}")
        return 1
    
    try:
        analyze_products_coverage(args.aoi, args.products)
        return 0
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
