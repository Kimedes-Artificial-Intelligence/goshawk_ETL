#!/usr/bin/env python3
"""
Script para seleccionar el sub-swath óptimo basado en cobertura del AOI.

MÉTODO: Usa intersección de bounding boxes desde archivos annotation/*.xml
Los archivos de anotación contienen las coordenadas geográficas precisas de cada subswath.

NOTA SOBRE VALIDACIÓN DE DATOS:
- Los archivos measurement/*.tiff están en coordenadas de RADAR (slant range), NO geográficas
- Por eso la validación de datos está DESHABILITADA por defecto
- La intersección de bbox desde annotation es suficiente y confiable
- Para detectar problemas de cobertura real, usar post-validación en calculate_pair_statistics.py

Analiza todos los productos SLC disponibles y determina qué sub-swath (IW1, IW2, IW3)
tiene la mejor cobertura del AOI. Luego selecciona el producto SLC apropiado para cada 
fecha basándose en ese sub-swath óptimo.

VALIDACIÓN DE COBERTURA:
- ✅ Intersección geométrica de bounding boxes (desde annotation/*.xml)
- ❌ Validación de datos reales (measurement en coordenadas radar - no aplicable)
- ✅ Post-validación después de procesar (en calculate_pair_statistics.py)

CAMBIOS vs VERSIÓN ANTERIOR:
1. validate_actual_data_coverage() implementada pero DESHABILITADA
2. Solo se usa intersección bbox (método original, confiable)
3. Advertencias de cobertura insuficiente en calculate_pair_statistics.py
"""

import os
import sys
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime
import xml.etree.ElementTree as ET
from typing import Dict, Set, List, Tuple, Optional
import numpy as np

# Importar utilidades de AOI
sys.path.insert(0, os.path.dirname(__file__))
try:
    from aoi_utils import geojson_to_bbox
except ImportError:
    print("Error: No se pudo importar aoi_utils.py")
    sys.exit(1)

# Intentar importar GDAL/rasterio para validar datos reales
try:
    from osgeo import gdal
    GDAL_AVAILABLE = True
except ImportError:
    try:
        import rasterio
        GDAL_AVAILABLE = True
    except ImportError:
        GDAL_AVAILABLE = False
        print("⚠️  ADVERTENCIA: GDAL/rasterio no disponible - solo se usará intersección geométrica")
        print("   Para validación completa de cobertura, instala: conda install -c conda-forge gdal")


def get_annotation_files(product_dir: Path, subswath: str) -> List[Path]:
    """
    Obtiene los archivos de anotación para un sub-swath específico.

    Args:
        product_dir: Directorio del producto .SAFE
        subswath: Sub-swath (e.g., 'IW1', 'IW2', 'IW3')

    Returns:
        Lista de archivos de anotación
    """
    annotation_dir = product_dir / "annotation"
    if not annotation_dir.exists():
        return []

    # Buscar archivos que contengan el sub-swath
    # Ejemplo: s1c-iw1-slc-vv-20250822t060010...xml
    pattern = f"*{subswath.lower()}*slc*.xml"
    annotation_files = list(annotation_dir.glob(pattern))

    return annotation_files


def extract_subswath_bounds(annotation_file: Path) -> Optional[Dict[str, float]]:
    """
    Extrae las coordenadas geográficas de un sub-swath desde su archivo de anotación.

    Returns:
        Dict con min_lon, max_lon, min_lat, max_lat o None si falla
    """
    try:
        tree = ET.parse(annotation_file)
        root = tree.getroot()

        # Buscar coordenadas en geolocationGrid
        # El XML tiene estructura: geolocationGrid/geolocationGridPointList/geolocationGridPoint
        lats = []
        lons = []

        for point in root.findall('.//geolocationGridPoint'):
            lat_elem = point.find('latitude')
            lon_elem = point.find('longitude')

            if lat_elem is not None and lon_elem is not None:
                try:
                    lat = float(lat_elem.text)
                    lon = float(lon_elem.text)
                    lats.append(lat)
                    lons.append(lon)
                except (ValueError, AttributeError):
                    continue

        if not lats or not lons:
            return None

        return {
            'min_lon': min(lons),
            'max_lon': max(lons),
            'min_lat': min(lats),
            'max_lat': max(lats)
        }

    except Exception as e:
        print(f"  ⚠️  Error leyendo {annotation_file.name}: {e}")
        return None


def check_bbox_intersection(bbox1: Dict[str, float], bbox2: Dict[str, float]) -> bool:
    """
    Verifica si dos bounding boxes se intersectan.

    Args:
        bbox1: Primer bbox (e.g., AOI)
        bbox2: Segundo bbox (e.g., sub-swath)

    Returns:
        True si hay intersección
    """
    # No hay intersección si:
    # - bbox1 está completamente a la derecha de bbox2
    # - bbox1 está completamente a la izquierda de bbox2
    # - bbox1 está completamente arriba de bbox2
    # - bbox1 está completamente abajo de bbox2

    if bbox1['min_lon'] > bbox2['max_lon']:
        return False
    if bbox1['max_lon'] < bbox2['min_lon']:
        return False
    if bbox1['min_lat'] > bbox2['max_lat']:
        return False
    if bbox1['max_lat'] < bbox2['min_lat']:
        return False

    return True


def calculate_intersection_area(bbox1: Dict[str, float], bbox2: Dict[str, float]) -> float:
    """
    Calcula el área de intersección entre dos bounding boxes (en grados cuadrados).

    Returns:
        Área de intersección (0 si no hay intersección)
    """
    if not check_bbox_intersection(bbox1, bbox2):
        return 0.0

    # Calcular bbox de intersección
    inter_min_lon = max(bbox1['min_lon'], bbox2['min_lon'])
    inter_max_lon = min(bbox1['max_lon'], bbox2['max_lon'])
    inter_min_lat = max(bbox1['min_lat'], bbox2['min_lat'])
    inter_max_lat = min(bbox1['max_lat'], bbox2['max_lat'])

    width = inter_max_lon - inter_min_lon
    height = inter_max_lat - inter_min_lat

    return width * height


def calculate_coverage_quality(aoi_bbox: Dict[str, float], subswath_bbox: Dict[str, float], 
                                intersection_area: float) -> Tuple[bool, float, str]:
    """
    Evalúa la calidad de cobertura del subswath sobre el AOI.
    
    Criterios:
    - Área de intersección mínima (evita overlaps marginales)
    - Porcentaje del AOI cubierto
    - Distancia a los bordes del subswath (zonas sin datos válidos)
    
    Returns:
        Tuple (is_acceptable: bool, quality_score: float, reason: str)
    """
    if intersection_area == 0:
        return (False, 0.0, "Sin intersección")
    
    # Calcular área del AOI
    aoi_width = aoi_bbox['max_lon'] - aoi_bbox['min_lon']
    aoi_height = aoi_bbox['max_lat'] - aoi_bbox['min_lat']
    aoi_area = aoi_width * aoi_height
    
    # Porcentaje del AOI cubierto
    coverage_pct = (intersection_area / aoi_area) * 100 if aoi_area > 0 else 0
    
    # Verificar tamaño mínimo de intersección (grados²)
    # AOI típico de pueblo: ~0.0001-0.01 grados² 
    # Intersección mínima requerida: 30% del AOI o 0.0001 grados² (lo que sea mayor)
    MIN_INTERSECTION_AREA = max(0.0001, aoi_area * 0.30)
    
    if intersection_area < MIN_INTERSECTION_AREA:
        return (False, coverage_pct, f"Intersección muy pequeña ({intersection_area:.6f}°² < {MIN_INTERSECTION_AREA:.6f}°²)")
    
    # Verificar que cubra al menos 30% del AOI (reducido de 50%)
    if coverage_pct < 30.0:
        return (False, coverage_pct, f"Cobertura insuficiente ({coverage_pct:.1f}% < 30%)")
    
    # Verificar distancia a bordes del subswath (detectar zonas marginales)
    # Calcular qué tan cerca está el AOI de los bordes del subswath
    swath_width = subswath_bbox['max_lon'] - subswath_bbox['min_lon']
    swath_height = subswath_bbox['max_lat'] - subswath_bbox['min_lat']
    
    # Distancia del AOI a cada borde del subswath
    dist_to_west = aoi_bbox['min_lon'] - subswath_bbox['min_lon']
    dist_to_east = subswath_bbox['max_lon'] - aoi_bbox['max_lon']
    dist_to_south = aoi_bbox['min_lat'] - subswath_bbox['min_lat']
    dist_to_north = subswath_bbox['max_lat'] - aoi_bbox['max_lat']
    
    # Margen seguro: al menos 5% del ancho/alto del subswath (reducido de 10%)
    margin_lon = swath_width * 0.05
    margin_lat = swath_height * 0.05
    
    # Verificar si está en zona marginal extrema (menos del 5% de margen a cualquier borde)
    is_marginal_west = dist_to_west < margin_lon
    is_marginal_east = dist_to_east < margin_lon
    is_marginal_south = dist_to_south < margin_lat
    is_marginal_north = dist_to_north < margin_lat
    
    # Solo rechazar si está muy cerca de AMBOS lados (este Y oeste, o norte Y sur)
    # Esto detecta AOIs justo en el borde extremo
    is_extreme_marginal = (is_marginal_west and is_marginal_east) or (is_marginal_south and is_marginal_north)
    
    if is_extreme_marginal:
        margins = []
        if is_marginal_west: margins.append("oeste")
        if is_marginal_east: margins.append("este")
        if is_marginal_south: margins.append("sur")
        if is_marginal_north: margins.append("norte")
        return (False, coverage_pct, f"AOI en zona marginal extrema del subswath (bordes {'/'.join(margins)})")
    
    # Todo OK
    quality_score = coverage_pct
    return (True, quality_score, "Cobertura aceptable")



def get_manifest_path(product_dir):
    """Obtiene la ruta al archivo manifest.safe del producto."""
    manifest = Path(product_dir) / "manifest.safe"
    if manifest.exists():
        return manifest
    return None


def extract_subswaths_from_manifest(manifest_path):
    """
    Extrae los sub-swaths disponibles del archivo manifest.safe.

    Returns:
        set: Conjunto de sub-swaths disponibles (e.g., {'IW1', 'IW2', 'IW3'})
    """
    try:
        import re

        tree = ET.parse(manifest_path)
        root = tree.getroot()

        subswaths = set()

        # Buscar dataObjectPointer en cualquier namespace
        # El patrón de ID es: products1ciw1slcvh... o products1ciw2slcvh...
        for elem in root.iter():
            # Buscar elementos que contengan 'dataobjectpointer' (lowercase) en su tag
            if 'dataobjectpointer' in elem.tag.lower():
                obj_id = elem.get('dataObjectID', '').lower()
                
                # Buscar patrones como iw1slc, iw2slc, iw3slc en el ID
                if 'slc' in obj_id and 'iw' in obj_id:
                    match = re.search(r'iw(\d)slc', obj_id)
                    if match:
                        swath_num = match.group(1)
                        subswath = f'IW{swath_num}'
                        subswaths.add(subswath)

        return subswaths
    except Exception as e:
        print(f"Error al leer manifest {manifest_path}: {e}")
        return set()


def get_product_date(product_name):
    """
    Extrae la fecha del nombre del producto SLC.

    Formato esperado: S1A_IW_SLC__1SDV_20241101T...
    """
    try:
        parts = product_name.split('_')
        for part in parts:
            if len(part) >= 8 and part[0:8].isdigit():
                date_str = part[0:8]
                return datetime.strptime(date_str, '%Y%m%d').date()
    except Exception as e:
        print(f"Error al extraer fecha de {product_name}: {e}")
    return None


def validate_actual_data_coverage(product_dir: Path, subswath: str, aoi_bbox: Dict[str, float], 
                                  min_coverage_pct: float = 50.0) -> Tuple[bool, float]:
    """
    Valida que el subswath tiene DATOS REALES válidos (no solo bbox) cubriendo el AOI.
    
    Lee el archivo de medición (measurement) del subswath y verifica el porcentaje
    de píxeles con datos válidos (no-zero, no-NaN) dentro del AOI.
    
    Args:
        product_dir: Directorio del producto .SAFE
        subswath: Sub-swath a verificar (e.g., 'IW1')
        aoi_bbox: Bounding box del AOI
        min_coverage_pct: Porcentaje mínimo de cobertura requerido (default 50%)
        
    Returns:
        Tuple (has_valid_coverage: bool, coverage_percentage: float)
    """
    if not GDAL_AVAILABLE:
        # Si no hay GDAL, solo retornar True (usar intersección geométrica)
        return (True, 100.0)
    
    try:
        # Buscar archivo de medición (measurement) para este subswath
        # Formato: s1c-iw1-slc-vv-20250822t060010-...tiff
        measurement_dir = product_dir / "measurement"
        if not measurement_dir.exists():
            return (True, 100.0)  # No puede validar, asumir OK
        
        # Buscar archivo TIFF del subswath (VV o VH polarización)
        pattern = f"*{subswath.lower()}-slc-vv*.tiff"
        measurement_files = list(measurement_dir.glob(pattern))
        
        if not measurement_files:
            # Intentar con VH si VV no existe
            pattern = f"*{subswath.lower()}-slc-vh*.tiff"
            measurement_files = list(measurement_dir.glob(pattern))
        
        if not measurement_files:
            return (True, 100.0)  # No puede validar, asumir OK
        
        measurement_file = measurement_files[0]
        
        # Abrir archivo con rasterio
        try:
            import rasterio
            from rasterio.windows import from_bounds
            
            with rasterio.open(measurement_file) as src:
                # Obtener ventana correspondiente al AOI
                try:
                    window = from_bounds(
                        aoi_bbox['min_lon'], 
                        aoi_bbox['min_lat'],
                        aoi_bbox['max_lon'], 
                        aoi_bbox['max_lat'],
                        transform=src.transform
                    )
                    
                    # Leer datos en la ventana del AOI (solo muestra para eficiencia)
                    # Usar step para no leer todos los píxeles
                    data = src.read(1, window=window)
                    
                    if data.size == 0:
                        return (False, 0.0)
                    
                    # Calcular porcentaje de píxeles válidos
                    # Válidos = no-NaN y no-cero (cero típicamente indica sin datos en SLC)
                    valid_mask = np.isfinite(data) & (data != 0)
                    valid_pixels = np.sum(valid_mask)
                    total_pixels = data.size
                    
                    coverage_pct = (valid_pixels / total_pixels) * 100.0
                    
                    has_coverage = coverage_pct >= min_coverage_pct
                    
                    return (has_coverage, coverage_pct)
                    
                except Exception as e:
                    # Error en window o read - probablemente AOI fuera del raster
                    return (False, 0.0)
                    
        except ImportError:
            # rasterio no disponible, retornar True
            return (True, 100.0)
            
    except Exception as e:
        # En caso de error, asumir que sí tiene cobertura (conservador)
        return (True, 100.0)


def analyze_subswath_coverage(product_dir: Path, subswath: str, aoi_bbox: Dict[str, float], 
                              validate_data: bool = False) -> Tuple[bool, float, Optional[float]]:
    """
    Analiza si un sub-swath específico de un producto cubre el AOI.
    
    NOTA: validate_data=False por defecto porque los archivos measurement/*.tiff
    están en coordenadas de radar (slant range), no geográficas.
    La validación geométrica desde annotation/*.xml es suficiente y confiable.

    Args:
        product_dir: Directorio del producto .SAFE
        subswath: Sub-swath a verificar (e.g., 'IW1')
        aoi_bbox: Bounding box del AOI
        validate_data: Si True, intenta validar con datos (EXPERIMENTAL, puede fallar)

    Returns:
        Tuple (intersects: bool, intersection_area: float, data_coverage_pct: Optional[float])
    """
    # Obtener archivos de anotación del sub-swath
    annotation_files = get_annotation_files(product_dir, subswath)

    if not annotation_files:
        return (False, 0.0, None)

    # Analizar el primer archivo de anotación (típicamente VV o VH)
    subswath_bbox = extract_subswath_bounds(annotation_files[0])

    if not subswath_bbox:
        return (False, 0.0, None)

    # Verificar intersección geométrica
    intersects = check_bbox_intersection(aoi_bbox, subswath_bbox)
    intersection_area = calculate_intersection_area(aoi_bbox, subswath_bbox)

    if not intersects:
        return (False, 0.0, None)
    
    # Evaluar calidad de cobertura (nuevo: detecta overlaps marginales)
    is_acceptable, quality_score, reason = calculate_coverage_quality(aoi_bbox, subswath_bbox, intersection_area)
    
    if not is_acceptable:
        # Intersección geométrica pero calidad insuficiente
        return (False, intersection_area, None)
    
    # Si hay intersección geométrica, validar datos reales
    data_coverage_pct = None
    if validate_data and GDAL_AVAILABLE:
        has_data, data_coverage_pct = validate_actual_data_coverage(product_dir, subswath, aoi_bbox)
        
        if not has_data:
            # Hay intersección geométrica pero NO hay datos válidos
            return (False, intersection_area, data_coverage_pct)
    
    return (True, intersection_area, data_coverage_pct)


def analyze_slc_products(slc_dir: str, aoi_geojson: str, verbose: bool = True):
    """
    Analiza todos los productos SLC y determina qué sub-swaths intersectan con el AOI.
    
    Args:
        slc_dir: Directorio con productos SLC
        aoi_geojson: Ruta al archivo GeoJSON del AOI
        verbose: Si True, muestra análisis detallado. Si False, solo procesa.

    Returns:
        dict: Información sobre productos y cobertura por sub-swath
    """
    slc_path = Path(slc_dir)

    if not slc_path.exists():
        print(f"Error: Directorio {slc_dir} no existe")
        return None

    # Cargar AOI
    try:
        aoi_bbox = geojson_to_bbox(aoi_geojson)
        if verbose:
            print(f"AOI: {aoi_bbox}")
    except Exception as e:
        print(f"Error cargando AOI: {e}")
        return None

    # Estructura para almacenar información
    products_by_date = defaultdict(list)
    subswath_coverage = defaultdict(set)  # subswath -> set of dates with coverage

    if verbose:
        print("\nAnalizando productos SLC y cobertura del AOI...")
        print("-" * 80)

    # Buscar todos los productos .SAFE
    safe_products = sorted([d for d in slc_path.iterdir() if d.is_dir() and d.name.endswith('.SAFE')])

    for product_dir in safe_products:
        product_name = product_dir.name

        # Extraer fecha
        product_date = get_product_date(product_name)
        if not product_date:
            continue

        # Obtener manifest
        manifest = get_manifest_path(product_dir)
        if not manifest:
            if verbose:
                print(f"⚠️  No se encontró manifest para {product_name}")
            continue

        # Extraer sub-swaths disponibles
        subswaths = extract_subswaths_from_manifest(manifest)

        if not subswaths:
            if verbose:
                print(f"⚠️  No se encontraron sub-swaths en {product_name}")
            continue

        if verbose:
            print(f"\n{product_date} - {product_name}")
            print(f"  Sub-swaths disponibles: {sorted(subswaths)}")

        # Verificar intersección con AOI para cada sub-swath
        subswaths_covering_aoi = set()
        subswath_coverage_details = {}  # Para almacenar % de cobertura

        for subswath in sorted(subswaths):
            # NOTA: validate_data=False porque measurement/*.tiff están en coordenadas radar
            # La validación desde annotation/*.xml (bbox) es suficiente y confiable
            intersects, area, data_coverage_pct = analyze_subswath_coverage(
                product_dir, subswath, aoi_bbox, validate_data=False
            )

            if intersects:
                subswaths_covering_aoi.add(subswath)
                subswath_coverage[subswath].add(product_date)
                subswath_coverage_details[subswath] = data_coverage_pct
                
                if verbose:
                    if data_coverage_pct is not None:
                        print(f"    ✓ {subswath}: Cubre AOI (intersección: {area:.6f}°², cobertura datos: {data_coverage_pct:.1f}%)")
                    else:
                        print(f"    ✓ {subswath}: Cubre AOI (intersección: {area:.6f}°²)")
            else:
                if verbose:
                    if data_coverage_pct is not None and data_coverage_pct < 50:
                        print(f"    ✗ {subswath}: Intersecta pero sin datos válidos (cobertura: {data_coverage_pct:.1f}%)")
                    else:
                        print(f"    ✗ {subswath}: NO cubre AOI")

        # Almacenar información del producto
        products_by_date[product_date].append({
            'product': product_name,
            'path': str(product_dir),
            'subswaths_available': subswaths,
            'subswaths_covering_aoi': subswaths_covering_aoi
        })

    return {
        'products_by_date': products_by_date,
        'subswath_coverage': subswath_coverage,
        'aoi_bbox': aoi_bbox
    }


def select_optimal_subswath(analysis):
    """
    Selecciona el sub-swath con mayor cobertura temporal del AOI.

    Returns:
        tuple: (optimal_subswath, coverage_count)
    """
    subswath_coverage = analysis['subswath_coverage']

    print("\n" + "=" * 80)
    print("ANÁLISIS DE COBERTURA POR SUB-SWATH (INTERSECCIÓN CON AOI)")
    print("=" * 80)

    coverage_summary = []
    for subswath in sorted(subswath_coverage.keys()):
        count = len(subswath_coverage[subswath])
        coverage_summary.append((subswath, count))
        print(f"{subswath}: {count} días con cobertura del AOI")

    if not coverage_summary:
        print("⚠️  Ningún sub-swath cubre el AOI")
        return None, 0

    # Seleccionar el sub-swath con mayor cobertura
    optimal_subswath, max_coverage = max(coverage_summary, key=lambda x: x[1])

    print("\n" + "-" * 80)
    print(f"✓ Sub-swath óptimo seleccionado: {optimal_subswath} ({max_coverage} días con cobertura)")
    print("-" * 80)

    return optimal_subswath, max_coverage


def select_products_for_subswath(analysis, optimal_subswath):
    """
    Selecciona los productos SLC cuyo sub-swath óptimo cubre el AOI.

    Returns:
        list: Lista de productos seleccionados
    """
    products_by_date = analysis['products_by_date']
    selected_products = []
    dates_without_coverage = []

    print("\n" + "=" * 80)
    print(f"SELECCIÓN DE PRODUCTOS PARA SUB-SWATH {optimal_subswath}")
    print("=" * 80)

    for date in sorted(products_by_date.keys()):
        products = products_by_date[date]

        # Buscar producto cuyo sub-swath óptimo cubra el AOI
        selected = None
        for product in products:
            if optimal_subswath in product['subswaths_covering_aoi']:
                selected = product
                break

        if selected:
            selected_products.append({
                'date': date.strftime('%Y-%m-%d'),
                'product': selected['product'],
                'path': selected['path'],
                'subswath': optimal_subswath,
                'all_subswaths_available': sorted(selected['subswaths_available']),
                'all_subswaths_covering_aoi': sorted(selected['subswaths_covering_aoi'])
            })
            print(f"✓ {date} - {selected['product']}")
        else:
            dates_without_coverage.append(date)
            print(f"✗ {date} - No hay producto con {optimal_subswath} cubriendo AOI")

    print("\n" + "-" * 80)
    print(f"Total productos seleccionados: {len(selected_products)}")
    print(f"Fechas sin cobertura en {optimal_subswath}: {len(dates_without_coverage)}")

    if dates_without_coverage:
        print("\nFechas sin cobertura:")
        for date in dates_without_coverage:
            print(f"  - {date}")

    return selected_products


def save_selection(selected_products, optimal_subswath, aoi_bbox, output_file):
    """Guarda la selección de productos en un archivo JSON."""
    output_data = {
        'optimal_subswath': optimal_subswath,
        'total_products': len(selected_products),
        'aoi_bbox': aoi_bbox,
        'products': selected_products,
        'analysis_date': datetime.now().isoformat()
    }

    with open(output_file, 'w') as f:
        json.dump(output_data, f, indent=2)

    print(f"\n✓ Selección guardada en: {output_file}")


def main():
    # Configuración
    base_dir = Path(__file__).parent.parent
    slc_dir = base_dir / "data" / "sentinel1_slc"
    aoi_geojson = base_dir / "aoi_arenys.geojson"
    output_file = base_dir / "selected_products_subswath.json"

    # Permitir parámetros opcionales
    if len(sys.argv) > 1:
        slc_dir = Path(sys.argv[1])
    if len(sys.argv) > 2:
        aoi_geojson = Path(sys.argv[2])
    if len(sys.argv) > 3:
        output_file = Path(sys.argv[3])

    print("=" * 80)
    print("SELECCIÓN DE SUB-SWATH ÓPTIMO BASADO EN COBERTURA DEL AOI")
    print("=" * 80)
    print(f"Directorio SLC: {slc_dir}")
    print(f"AOI GeoJSON: {aoi_geojson}")
    print(f"Archivo de salida: {output_file}")
    print()

    # Verificar que el AOI existe
    if not aoi_geojson.exists():
        print(f"Error: No existe el archivo AOI: {aoi_geojson}")
        return 1

    # Analizar productos
    analysis = analyze_slc_products(str(slc_dir), str(aoi_geojson))

    if not analysis:
        print("Error: No se pudo completar el análisis")
        return 1

    if not analysis['products_by_date']:
        print("Error: No se encontraron productos SLC")
        return 1

    # Seleccionar sub-swath óptimo
    optimal_subswath, coverage = select_optimal_subswath(analysis)

    if not optimal_subswath:
        print("Error: Ningún sub-swath cubre el AOI")
        return 1

    # Seleccionar productos para el sub-swath óptimo
    selected_products = select_products_for_subswath(analysis, optimal_subswath)

    if not selected_products:
        print("Error: No se seleccionaron productos")
        return 1

    # Guardar selección
    save_selection(selected_products, optimal_subswath, analysis['aoi_bbox'], output_file)

    print("\n" + "=" * 80)
    print("RESUMEN")
    print("=" * 80)
    print(f"Sub-swath óptimo: {optimal_subswath}")
    print(f"Productos seleccionados: {len(selected_products)}")
    print(f"Cobertura temporal: {coverage} días")
    print("\nPróximos pasos:")
    print(f"  1. Revisar el archivo: {output_file}")
    print(f"  2. Usar estos productos para procesamiento InSAR")
    print(f"  3. Configurar pipeline para usar sub-swath {optimal_subswath}")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
