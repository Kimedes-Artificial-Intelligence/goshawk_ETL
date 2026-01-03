#!/usr/bin/env python3
"""
Script: preprocess_products.py
Descripción: Pre-procesa productos Sentinel-1 con PyroSAR: recorte al AOI y creación de mosaicos
Uso: 
  python scripts/preprocess_products.py [--grd|--slc] [--insar-mode]

Modos de preprocesamiento:
  GRD: Read → Subset
  SLC Normal: Read → TOPSAR-Split → Subset → TOPSAR-Deburst
  SLC InSAR: Read → TOPSAR-Split → Apply-Orbit-File (sin Deburst ni Subset)

CORRECCIONES APLICADAS (2024-12-23/24):
  ✓ Reordenado operadores SLC Normal: Subset ANTES de Deburst (evita artefactos en bordes)
  ✓ Detección automática de polarizaciones disponibles (VV, VH, VV+VH)
  ✓ Eliminada restricción artificial de IW3 (todos los subswaths son válidos)
  ✓ Procesamiento secuencial (evita Out-Of-Memory: GPT ya paraleliza internamente)
  ✓ Agregado Apply-Orbit-File en modo InSAR (según workflow oficial ESA/SNAP)
  
IMPORTANTE - Modo InSAR (según tutoriales oficiales ESA):
  ⚠️  NO se aplica Subset geográfico en preprocesamiento InSAR
  ⚠️  Razón: SLC está en geometría radar (slant-range), no geográfica
  ⚠️  El Subset se debe hacer DESPUÉS de Back-Geocoding en el pipeline principal
  ✓  Apply-Orbit-File mejora precisión orbital para Back-Geocoding posterior

Este script:
1. Identifica productos Sentinel-1 descargados
2. Recorta productos al AOI usando PyroSAR
3. Aplica TOPSAR-Deburst solo en modo SAR normal (NO en modo InSAR)
4. Crea mosaicos de productos que cubren la misma fecha/órbita
5. Guarda productos pre-procesados listos para procesamiento SAR/InSAR
"""

import argparse
import glob
import logging
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from shapely import wkt
from shapely.geometry import Polygon
from shapely.geometry import box

# Agregar directorio scripts al path si es necesario
sys.path.insert(0, str(Path(__file__).parent))
from logging_utils import LoggerConfig

# Predefinir nombres de módulos/imports para silenciar advertencias estáticas
pyroSAR = None
identify = None
geocode = None
shape = None
shapely_wkt = None
gpd = None

# PyroSAR es opcional
pyroSAR_available = False
try:
    import pyroSAR
    from pyroSAR import identify
    from pyroSAR.snap import geocode
    pyroSAR_available = True
except ImportError:
    pyroSAR = None
    identify = None
    geocode = None

try:
    from shapely.geometry import shape
    from shapely import wkt as shapely_wkt
    import geopandas as gpd
except ImportError as e:
    print(f"ERROR: No se pudo importar shapely/geopandas: {e}")
    print("Ejecuta: conda install -c conda-forge shapely geopandas")
    sys.exit(1)

# Configurar logging
logger = LoggerConfig.setup_script_logger(
    script_name='preprocess_products',
    log_dir='logs',
    level=logging.INFO,
    console_level=logging.WARNING  # Menos verbose en consola
)


def load_config(config_file="config.txt"):
    """Cargar configuración desde config.txt"""
    config = {
        'SLC_DIR': 'data/sentinel1_slc',
        'OUTPUT_DIR': 'processed',
        'AOI': ''
    }

    try:
        with open(config_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    config[key.strip()] = value.strip().strip('"')

        return config
    except Exception as e:
        logger.error(f"Error cargando configuración: {e}")
        return {}


def extract_date_from_filename(filename):
    """Extrae la fecha del nombre del archivo Sentinel-1"""
    match = re.search(r'(\d{8}T\d{6})', filename)
    if match:
        return match.group(1)
    return None


def extract_orbit_from_filename(filename):
    """Extrae el número de órbita relativa del nombre del archivo Sentinel-1"""
    # Formato: S1A_IW_GRDH_1SDV_20200101T123456_20200101T123521_030719_038A7E_1234.SAFE
    # El número de órbitas está en la posición 7 (030719 en este ejemplo)
    parts = filename.split('_')
    if len(parts) >= 7:
        try:
            # Los primeros 3 dígitos del absolute orbit number dan el relative orbit
            orbit_abs = int(parts[6])
            # Para Sentinel-1, relative orbit = (absolute orbit - 73) % 175 + 1
            orbit_rel = ((orbit_abs - 73) % 175) + 1
            return orbit_rel
        except (ValueError, IndexError):
            pass
    return None


def group_products_by_date_orbit(products):
    """
    Agrupa productos por fecha y órbita para identificar candidatos a mosaico

    Returns:
        dict: {(date, orbit): [list of product paths]}
    """
    groups = defaultdict(list)

    for product in products:
        basename = os.path.basename(product)
        date_str = extract_date_from_filename(basename)
        orbit = extract_orbit_from_filename(basename)

        if date_str and orbit:
            # Usar solo la fecha (YYYYMMDD), ignorar tiempo
            date_key = date_str[:8]
            groups[(date_key, orbit)].append(product)
        else:
            logger.warning(f"No se pudo extraer fecha/órbita de: {basename}")

    return groups


def wkt_to_shapefile(wkt_string, output_shapefile):
    """
    Convierte un WKT polygon a shapefile

    Args:
        wkt_string: String WKT del polígono
        output_shapefile: Ruta de salida para el shapefile

    Returns:
        str: Ruta al shapefile creado o None si falla
    """
    try:
        # Parsear WKT
        geom = shapely_wkt.loads(wkt_string)

        # Crear GeoDataFrame
        gdf = gpd.GeoDataFrame({'geometry': [geom]}, crs='EPSG:4326')

        # Guardar como shapefile
        gdf.to_file(output_shapefile)

        logger.debug(f"  ✓ Shapefile creado: {output_shapefile}")
        return output_shapefile

    except Exception as e:
        logger.error(f"  ✗ Error creando shapefile: {e}")
        return None


def identify_product(product_path):
    """
    Identifica un producto Sentinel-1 usando PyroSAR

    Returns:
        pyroSAR.drivers.ID object or None
    """
    try:
        logger.debug(f"Identificando producto: {os.path.basename(product_path)}")
        product_id = identify(product_path)

        if product_id:
            logger.debug(f"  Satélite: {product_id.sensor}")
            logger.debug(f"  Modo: {product_id.acquisition_mode}")
            logger.debug(f"  Producto: {product_id.product}")
            logger.debug(f"  Fecha: {product_id.start}")
            logger.debug(f"  Órbita: {product_id.orbit}")
            return product_id
        else:
            logger.warning(f"  No se pudo identificar el producto")
            return None

    except Exception as e:
        logger.error(f"  Error identificando producto: {e}")
        return None


def check_burst_coverage(product_path, aoi_wkt):
    """
    Verifica qué porcentaje del AOI cubre un burst
    
    Args:
        product_path: Ruta al producto .SAFE
        aoi_wkt: AOI en formato WKT
        
    Returns:
        float: Porcentaje de cobertura del AOI (0-100)
    """
    try:
        # Leer manifest del producto
        manifest_path = os.path.join(product_path, 'manifest.safe')
        if not os.path.exists(manifest_path):
            logger.warning(f"  No existe manifest: {manifest_path}")
            return 0.0
            
        tree = ET.parse(manifest_path)
        root = tree.getroot()
        
        # Buscar footprint del producto
        ns = {'gml': 'http://www.opengis.net/gml'}
        coords_elem = root.find('.//{http://www.opengis.net/gml}coordinates')
        
        if coords_elem is None:
            logger.warning(f"  No se encontró footprint en manifest")
            return 0.0
            
        # Parsear coordenadas (formato: lat1,lon1 lat2,lon2 ...)
        coords_text = coords_elem.text.strip()
        coord_pairs = coords_text.split()
        
        points = []
        for pair in coord_pairs:
            parts = pair.split(',')
            if len(parts) == 2:
                lat, lon = float(parts[0]), float(parts[1])
                points.append((lon, lat))  # Shapely usa (lon, lat)
        
        if len(points) < 3:
            logger.warning(f"  Footprint inválido (menos de 3 puntos)")
            return 0.0
            
        # Crear polígonos
        burst_poly = Polygon(points)
        aoi_poly = wkt.loads(aoi_wkt)
        
        # Calcular intersección
        intersection = burst_poly.intersection(aoi_poly)
        coverage = (intersection.area / aoi_poly.area) * 100
        
        return coverage
        
    except Exception as e:
        logger.error(f"  Error calculando cobertura: {e}")
        return 0.0


def detect_best_subswath_for_aoi(product_path, aoi_wkt):
    """
    Detecta qué sub-swath (IW1, IW2, IW3) contiene mejor el AOI

    Para productos SLC, cada sub-swath cubre una franja diferente.
    Esta función analiza el annotation de cada sub-swath para determinar
    cuál intersecta mejor con el AOI.

    Args:
        product_path: Ruta al producto .SAFE
        aoi_wkt: AOI en formato WKT

    Returns:
        str: 'IW1', 'IW2' o 'IW3' - el sub-swath con mejor cobertura
    """
    # Usar la nueva función multi-subswath y retornar el mejor
    subswaths = detect_all_subswaths_for_aoi(product_path, aoi_wkt)
    if subswaths:
        # Retornar el primero (ordenados por cobertura descendente)
        return subswaths[0][0]
    else:
        logger.warning(f"    No se pudo detectar sub-swath, usando IW1 por defecto")
        return 'IW1'


def detect_all_subswaths_for_aoi(product_path, aoi_wkt, min_coverage=5.0):
    """
    NUEVO: Detecta TODOS los sub-swaths que intersectan con el AOI

    MODIFICACIÓN PARA DETECCIÓN DE HUMEDAD:
    En lugar de seleccionar solo el "mejor" subswath, esta función retorna
    TODOS los que tienen cobertura >= min_coverage. Esto evita "puntos ciegos"
    cuando un pueblo está en la frontera entre subswaths.

    Args:
        product_path: Ruta al producto .SAFE
        aoi_wkt: AOI en formato WKT
        min_coverage: Cobertura mínima (%) para considerar un subswath

    Returns:
        list: Lista de tuplas [(subswath, coverage), ...] ordenadas por cobertura desc
              Solo incluye subswaths con cobertura >= min_coverage
              Ej: [('IW2', 85.3), ('IW1', 45.2)]
    """
    try:
        aoi_poly = wkt.loads(aoi_wkt)

        # Buscar archivos de annotation para cada sub-swath
        annotation_dir = os.path.join(product_path, 'annotation')

        if not os.path.isdir(annotation_dir):
            logger.warning(f"  No existe directorio annotation en {product_path}")
            return [('IW1', 0.0)]  # Default fallback

        # Lista para almacenar todos los subswaths con cobertura
        subswath_coverages = []

        # Analizar TODOS los subswaths IW1, IW2, IW3 (IW3 es válido para InSAR)
        for subswath in ['IW1', 'IW2', 'IW3']:
            # Buscar archivo annotation del sub-swath
            import glob
            pattern = os.path.join(annotation_dir, f's1*-{subswath.lower()}-slc-*.xml')
            annotation_files = glob.glob(pattern)

            if not annotation_files:
                continue

            annotation_file = annotation_files[0]

            try:
                tree = ET.parse(annotation_file)
                root = tree.getroot()

                # Extraer coordenadas del sub-swath desde geolocationGrid
                lat_values = []
                lon_values = []

                for geolocation_point in root.findall('.//geolocationGridPoint'):
                    lat_elem = geolocation_point.find('latitude')
                    lon_elem = geolocation_point.find('longitude')
                    if lat_elem is not None and lon_elem is not None:
                        lat_values.append(float(lat_elem.text))
                        lon_values.append(float(lon_elem.text))

                if lat_values and lon_values:
                    # Crear bounding box del sub-swath
                    swath_bounds = (
                        min(lon_values),  # minx
                        min(lat_values),  # miny
                        max(lon_values),  # maxx
                        max(lat_values)   # maxy
                    )

                    swath_box = box(*swath_bounds)

                    # Calcular intersección con AOI
                    intersection = swath_box.intersection(aoi_poly)
                    coverage = (intersection.area / aoi_poly.area) * 100 if intersection.area > 0 else 0.0

                    logger.debug(f"    {subswath}: cobertura {coverage:.1f}%")

                    # MODIFICADO: Agregar a lista si cumple cobertura mínima
                    if coverage >= min_coverage:
                        subswath_coverages.append((subswath, coverage))

            except Exception as e:
                logger.debug(f"    Error analizando {subswath}: {e}")
                continue

        # Ordenar por cobertura descendente
        subswath_coverages.sort(key=lambda x: x[1], reverse=True)

        if subswath_coverages:
            logger.info(f"    → Sub-swaths detectados: {[(s, f'{c:.1f}%') for s, c in subswath_coverages]}")
            if len(subswath_coverages) > 1:
                total_coverage = sum(c for _, c in subswath_coverages)
                logger.info(f"    → Cobertura combinada: {min(total_coverage, 100):.1f}%")
                logger.info(f"    ⚠️  AOI en frontera de subswaths - considerar procesar ambos")
            return subswath_coverages
        else:
            logger.warning(f"    No se encontraron sub-swaths con cobertura >= {min_coverage}%")
            return [('IW1', 0.0)]

    except Exception as e:
        logger.error(f"  Error detectando sub-swaths: {e}")
        return [('IW1', 0.0)]  # Default fallback


def get_burst_indices_for_aoi(product_path, subswath, aoi_wkt):
    """
    NUEVO: Calcula los índices de burst (firstBurstIndex, lastBurstIndex) que
    intersectan con el AOI.

    OPTIMIZACIÓN PARA DETECCIÓN DE HUMEDAD:
    En lugar de procesar toda la faja del subswath, esta función identifica
    solo los bursts necesarios. Esto:
    - Reduce tiempo de procesamiento
    - Reduce uso de memoria
    - Evita procesar datos innecesarios

    IMPORTANTE: Se mantiene la prohibición del operador Subset geográfico
    ANTES del Back-Geocoding para InSAR.

    Args:
        product_path: Ruta al producto .SAFE
        subswath: Sub-swath a analizar ('IW1', 'IW2', 'IW3')
        aoi_wkt: AOI en formato WKT

    Returns:
        tuple: (firstBurstIndex, lastBurstIndex) - índices 1-based para SNAP
               o (None, None) si no se pueden determinar
    """
    try:
        aoi_poly = wkt.loads(aoi_wkt)

        # Buscar archivo annotation del sub-swath
        annotation_dir = os.path.join(product_path, 'annotation')
        import glob
        pattern = os.path.join(annotation_dir, f's1*-{subswath.lower()}-slc-*.xml')
        annotation_files = glob.glob(pattern)

        if not annotation_files:
            logger.warning(f"  No se encontró annotation para {subswath}")
            return (None, None)

        annotation_file = annotation_files[0]
        tree = ET.parse(annotation_file)
        root = tree.getroot()

        # Extraer información de geolocalización por burst
        # Cada burst tiene múltiples puntos en geolocationGrid
        geolocation_points = root.findall('.//geolocationGridPoint')

        if not geolocation_points:
            logger.warning(f"  No se encontraron puntos de geolocalización")
            return (None, None)

        # Agrupar puntos por línea azimuth (cada burst tiene ~21 líneas)
        # El atributo 'line' indica la línea dentro del burst
        burst_lines = {}
        for point in geolocation_points:
            azimuth_time = point.find('azimuthTime')
            lat = point.find('latitude')
            lon = point.find('longitude')
            line_elem = point.find('line')

            if all(elem is not None for elem in [lat, lon, line_elem]):
                line_num = int(line_elem.text)
                if line_num not in burst_lines:
                    burst_lines[line_num] = []
                burst_lines[line_num].append((float(lon.text), float(lat.text)))

        # Obtener número de bursts desde swathTiming
        swath_timing = root.find('.//swathTiming')
        if swath_timing is not None:
            burst_list = swath_timing.find('burstList')
            if burst_list is not None:
                num_bursts = int(burst_list.get('count', 0))
                logger.debug(f"    {subswath}: {num_bursts} bursts")

        # Por ahora, sin información detallada de burst footprints,
        # retornamos None para usar todos los bursts
        # (implementación completa requeriría parsear burstList)
        logger.debug(f"    Selección de bursts: usando todos los bursts del subswath")
        return (None, None)

    except Exception as e:
        logger.warning(f"  Error calculando burst indices: {e}")
        return (None, None)




def select_best_burst_for_aoi(products, aoi_wkt, min_coverage=1.0):
    """
    Selecciona el mejor burst para cubrir el AOI
    
    Args:
        products: Lista de rutas a productos .SAFE
        aoi_wkt: AOI en formato WKT
        min_coverage: Cobertura mínima requerida (%)
        
    Returns:
        str: Ruta al mejor producto o None
    """
    if not products:
        return None
        
    if len(products) == 1:
        # Solo un burst, verificar cobertura
        coverage = check_burst_coverage(products[0], aoi_wkt)
        logger.info(f"    Un solo burst disponible: cobertura {coverage:.1f}%")
        if coverage >= min_coverage:
            return products[0]
        else:
            logger.warning(f"    Cobertura insuficiente ({coverage:.1f}% < {min_coverage}%)")
            return products[0]  # Devolver de todas formas
    
    # Múltiples bursts: seleccionar el de mejor cobertura
    best_product = None
    best_coverage = 0.0
    
    logger.info(f"    Evaluando {len(products)} bursts:")
    for product in products:
        basename = os.path.basename(product)
        coverage = check_burst_coverage(product, aoi_wkt)
        logger.info(f"      - {basename[:60]}: {coverage:.1f}% cobertura")
        
        if coverage > best_coverage:
            best_coverage = coverage
            best_product = product
    
    if best_coverage >= min_coverage:
        logger.info(f"    ✓ Seleccionado: {os.path.basename(best_product)[:60]} ({best_coverage:.1f}%)")
    else:
        logger.warning(f"    ⚠ Mejor cobertura: {best_coverage:.1f}% < {min_coverage}%")
        logger.warning(f"    Se necesitaría fusión de bursts para cobertura completa")
    
    return best_product


def detect_available_polarizations(product_path):
    """
    Detecta las polarizaciones disponibles en un producto Sentinel-1
    
    Args:
        product_path: Ruta al producto .SAFE
        
    Returns:
        str: String con polarizaciones disponibles ('VV', 'VH', 'VV,VH')
    """
    try:
        manifest_path = os.path.join(product_path, 'manifest.safe')
        if not os.path.exists(manifest_path):
            logger.debug(f"  No existe manifest, usando VV por defecto")
            return 'VV'
        
        tree = ET.parse(manifest_path)
        root = tree.getroot()
        
        # Buscar polarizaciones en manifest
        ns = {'s1sarl1': 'http://www.esa.int/safe/sentinel-1.0/sentinel-1/sar/level-1'}
        polarizations = set()
        
        for elem in root.findall('.//s1sarl1:transmitterReceiverPolarisation', ns):
            pol = elem.text
            if pol:
                polarizations.add(pol)
        
        if not polarizations:
            logger.debug(f"  No se detectaron polarizaciones, usando VV por defecto")
            return 'VV'
        
        # Ordenar para consistencia: VV primero, luego VH
        pols_sorted = sorted(list(polarizations), key=lambda x: (x != 'VV', x))
        pol_string = ','.join(pols_sorted)
        
        logger.debug(f"  Polarizaciones detectadas: {pol_string}")
        return pol_string
        
    except Exception as e:
        logger.debug(f"  Error detectando polarizaciones: {e}, usando VV por defecto")
        return 'VV'


def subset_product(product_path, aoi_wkt, output_dir, insar_mode=False, subswath=None):
    """
    Recorta un producto al AOI usando SNAP/GPT directamente (solo subset, sin procesamiento)

    Args:
        product_path: Ruta al producto .SAFE
        aoi_wkt: AOI en formato WKT (opcional en modo InSAR sin AOI)
        output_dir: Directorio de salida para producto recortado
        insar_mode: Si True, no aplica TOPSAR-Deburst (mantiene estructura de bursts para InSAR)
        subswath: Subswath específico (IW1/IW2/IW3) para modo InSAR sin AOI

    Returns:
        str: Ruta al producto recortado o None si falla
    """
    try:
        basename = os.path.basename(product_path)
        date_str = extract_date_from_filename(basename)

        # Determinar tipo de producto
        product_type = 'SLC'
        
        # Detectar polarizaciones disponibles
        polarizations = detect_available_polarizations(product_path)

        # Nombre de salida
        # Usar el basename (sin extensión) para evitar colisiones cuando hay varios
        # productos del mismo día (por ejemplo, varios SLC del mismo date).
        base_noext = os.path.splitext(basename)[0]
        suffix = '_split' if insar_mode else '_subset'
        output_name = f"{product_type}_{date_str[:8]}_{base_noext}{suffix}"
        output_path = os.path.join(output_dir, output_name + '.dim')

        # Verificar si ya existe
        if os.path.exists(output_path):
            logger.info(f"  ✓ Ya existe: {output_name}")
            return output_path

        if insar_mode:
            logger.info(f"  Procesando producto en modo InSAR (TOPSAR-Split + Apply-Orbit-File)...")
        else:
            logger.info(f"  Recortando producto al AOI con SNAP...")
        logger.info(f"  → Salida: {output_path}")

        # Crear XML según tipo de producto
        if product_type == 'SLC':
            # Detectar subswath
            if subswath:
                # Usar subswath especificado (cualquier IW1/IW2/IW3 es válido)
                best_subswath = subswath.upper()
            elif aoi_wkt:
                # Detectar qué sub-swath contiene el AOI
                best_subswath = detect_best_subswath_for_aoi(product_path, aoi_wkt)
            else:
                # Por defecto IW2 (mejor compromiso rango cercano/lejano)
                logger.warning("  ⚠️  Sin AOI ni subswath especificado, usando IW2")
                best_subswath = "IW2"
            
            logger.info(f"  Subswath seleccionado: {best_subswath}, Polarizaciones: {polarizations}")
            
            if insar_mode:
                # Para InSAR: TOPSAR-Split → Apply-Orbit-File (SIN Deburst, SIN Subset)
                # WORKFLOW OFICIAL según tutoriales ESA/SNAP:
                # - Apply-Orbit-File mejora precisión orbital para Back-Geocoding posterior
                # - El Subset geográfico NO funciona en geometría radar (slant-range)
                # - El subset se debe hacer DESPUÉS de Back-Geocoding en el pipeline InSAR principal
                # - Aquí solo preparamos productos con estructura de bursts intacta
                subset_xml = f"""<graph id="SLC_Split_ApplyOrbit_InSAR">
  <version>1.0</version>

  <node id="Read">
    <operator>Read</operator>
    <sources/>
    <parameters>
      <file>{product_path}</file>
    </parameters>
  </node>

  <node id="TOPSAR-Split">
    <operator>TOPSAR-Split</operator>
    <sources>
      <sourceProduct refid="Read"/>
    </sources>
    <parameters>
      <subswath>{best_subswath}</subswath>
      <selectedPolarisations>{polarizations}</selectedPolarisations>
    </parameters>
  </node>

  <node id="Apply-Orbit-File">
    <operator>Apply-Orbit-File</operator>
    <sources>
      <sourceProduct refid="TOPSAR-Split"/>
    </sources>
    <parameters>
      <orbitType>Sentinel Precise (Auto Download)</orbitType>
      <polyDegree>3</polyDegree>
      <continueOnFail>false</continueOnFail>
    </parameters>
  </node>

  <node id="Write">
    <operator>Write</operator>
    <sources>
      <sourceProduct refid="Apply-Orbit-File"/>
    </sources>
    <parameters>
      <file>{output_path}</file>
      <formatName>BEAM-DIMAP</formatName>
    </parameters>
  </node>

</graph>"""
            else:
                # Para SAR normal: TOPSAR-Split → Subset → TOPSAR-Deburst
                # CORRECCIÓN CRÍTICA: Subset ANTES de Deburst para evitar artefactos en bordes
                subset_xml = f"""<graph id="SLC_Split_Subset_Deburst">
  <version>1.0</version>

  <node id="Read">
    <operator>Read</operator>
    <sources/>
    <parameters>
      <file>{product_path}</file>
    </parameters>
  </node>

  <node id="TOPSAR-Split">
    <operator>TOPSAR-Split</operator>
    <sources>
      <sourceProduct refid="Read"/>
    </sources>
    <parameters>
      <subswath>{best_subswath}</subswath>
      <selectedPolarisations>{polarizations}</selectedPolarisations>
    </parameters>
  </node>

  <node id="Subset">
    <operator>Subset</operator>
    <sources>
      <sourceProduct refid="TOPSAR-Split"/>
    </sources>
    <parameters>
      <geoRegion>{aoi_wkt}</geoRegion>
      <copyMetadata>true</copyMetadata>
    </parameters>
  </node>

  <node id="TOPSAR-Deburst">
    <operator>TOPSAR-Deburst</operator>
    <sources>
      <sourceProduct refid="Subset"/>
    </sources>
    <parameters>
      <selectedPolarisations>{polarizations}</selectedPolarisations>
    </parameters>
  </node>

  <node id="Write">
    <operator>Write</operator>
    <sources>
      <sourceProduct refid="TOPSAR-Deburst"/>
    </sources>
    <parameters>
      <file>{output_path}</file>
      <formatName>BEAM-DIMAP</formatName>
    </parameters>
  </node>

</graph>"""


        # Guardar XML temporal
        with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as tf:
            tf.write(subset_xml)
            xml_file = tf.name

        try:
            # Ejecutar GPT
            logger.info(f"  ⚙️  Ejecutando GPT para subset...")
            result = subprocess.run(
                ['gpt', xml_file, '-c', '4G'],
                capture_output=True,
                text=True,
                timeout=600  # 10 minutos timeout
            )

            # Verificar que el archivo se creó correctamente
            if os.path.exists(output_path):
                logger.info(f"  ✓ Subset creado: {output_path}")
                return output_path
            else:
                logger.error(f"  ✗ Error: GPT terminó pero no creó el archivo (exit code {result.returncode})")
                if result.stdout:
                    logger.error(f"STDOUT: {result.stdout[-1000:]}")
                if result.stderr:
                    logger.error(f"STDERR: {result.stderr[-1000:]}")
                return None

        finally:
            # Limpiar XML temporal
            if os.path.exists(xml_file):
                os.unlink(xml_file)

    except Exception as e:
        logger.error(f"  ✗ Error recortando producto: {e}")
        logger.exception("Detalles:")
        return None


def create_mosaic(product_list, aoi_wkt, output_dir):
    """
    Crea un mosaico de múltiples productos Sentinel-1

    Args:
        product_list: Lista de rutas a productos .SAFE
        aoi_wkt: AOI en formato WKT
        output_dir: Directorio de salida

    Returns:
        str: Ruta al mosaico o None si falla
    """
    try:
        if len(product_list) < 2:
            logger.warning("  Se necesitan al menos 2 productos para crear mosaico")
            return None

        # Extraer información del primer producto
        basename = os.path.basename(product_list[0])
        date_str = extract_date_from_filename(basename)
        orbit = extract_orbit_from_filename(basename)
        product_type = 'SLC'

        mosaic_name = f"{product_type}_{date_str[:8]}_orbit{orbit:03d}_mosaic"
        output_path = os.path.join(output_dir, mosaic_name + '.dim')

        # Verificar si ya existe
        if os.path.exists(output_path):
            logger.info(f"  ✓ Ya existe mosaico: {mosaic_name}")
            return output_path

        logger.info(f"  Creando mosaico de {len(product_list)} productos...")
        logger.info(f"  → Productos: {[os.path.basename(p)[:40] + '...' for p in product_list]}")
        logger.info(f"  → Salida: {output_path}")

        # PyroSAR no tiene función directa de mosaico, tendríamos que usar SNAP directamente
        # Por ahora, procesaremos productos individualmente
        logger.warning("  Funcionalidad de mosaico no implementada aún")
        logger.warning("  Los productos serán procesados individualmente")

        return None

    except Exception as e:
        logger.error(f"  ✗ Error creando mosaico: {e}")
        logger.exception("Detalles:")
        return None


def process_single_product(args_tuple):
    """
    Procesa un producto individual (función helper para paralelización)

    Args:
        args_tuple: (index, total, product, aoi_wkt, output_dir, insar_mode, subswath_from_dir)

    Returns:
        tuple: (product_name, success)
    """
    index, total, product, aoi_wkt, output_dir, insar_mode, subswath_from_dir = args_tuple

    # Importar logger y función subset_product localmente
    from processing_utils import logger
    import os

    product_name = os.path.basename(product)[:60]
    logger.info(f"[{index}/{total}] {product_name}...")

    result = subset_product(product, aoi_wkt, output_dir, insar_mode=insar_mode, subswath=subswath_from_dir)

    return product_name, result


def preprocess_products(product_dir, output_dir, aoi_wkt, product_type='GRD', create_mosaics=False, insar_mode=False):
    """
    Pre-procesa productos Sentinel-1

    Args:
        product_dir: Directorio con productos .SAFE
        output_dir: Directorio de salida
        aoi_wkt: AOI en formato WKT (puede ser None en modo InSAR)
        product_type: 'GRD' or 'SLC'
        create_mosaics: Si True, crea mosaicos de productos con misma fecha/órbita
        insar_mode: Si True (solo SLC), no aplica Deburst (mantiene bursts para InSAR)
    """
    logger.info("=" * 80)
    logger.info(f"PRE-PROCESAMIENTO DE PRODUCTOS {product_type}")
    if product_type == 'SLC' and insar_mode:
        logger.info("MODO: InSAR (sin TOPSAR-Deburst, sin AOI)")
    elif product_type == 'SLC':
        logger.info("MODO: SAR Normal (con TOPSAR-Deburst)")
    logger.info("=" * 80)
    
    # Detectar subswath del nombre del directorio (para modo InSAR sin AOI)
    subswath_from_dir = None
    if insar_mode and not aoi_wkt:
        # Formato: data/preprocessed_slc_insar/desc_iw1/ → IW1
        match = re.search(r'_(iw[123])$', os.path.basename(output_dir.rstrip('/')))
        if match:
            subswath_from_dir = match.group(1).upper()  # IW1, IW2, IW3
            logger.info(f"Subswath detectado del directorio: {subswath_from_dir}")

    # Buscar productos
    pattern = os.path.join(product_dir, 'S1*_IW_SLC__*.SAFE')
    products = sorted(glob.glob(pattern))

    if not products:
        logger.warning(f"No se encontraron productos {product_type} en {product_dir}")
        return

    logger.info(f"Encontrados {len(products)} productos {product_type}")
    logger.info(f"  {len(products)} productos para pre-procesar")

    # Crear directorio de salida
    os.makedirs(output_dir, exist_ok=True)

    # Para SLC: agrupar por fecha y seleccionar mejor burst
    # (no crear mosaicos, pero sí seleccionar el burst con mejor cobertura)
    if product_type == 'SLC':
        logger.info('Para SLC: agrupando por fecha y seleccionando mejor burst por cobertura AOI...')
        create_mosaics = False
        
        # Agrupar por fecha (ignorar órbita)
        by_date = defaultdict(list)
        for product in products:
            basename = os.path.basename(product)
            date_str = extract_date_from_filename(basename)
            if date_str:
                date_key = date_str[:8]
                by_date[date_key].append(product)
        
        logger.info(f'  Fechas únicas: {len(by_date)}')
        
        # Seleccionar mejor burst por fecha
        selected_products = []
        for date, date_products in sorted(by_date.items()):
            if len(date_products) > 1:
                logger.info(f'  Fecha {date}: {len(date_products)} slices detectados (Frontera Azimutal)')
                # Añadir TODOS los que tengan cobertura mínima (> 1%)
                for p in date_products:
                    cov = check_burst_coverage(p, aoi_wkt)
                    if cov > 1.0:
                        logger.info(f"    -> Agregando slice con cobertura {cov:.1f}%")
                        selected_products.append(p)
                    else:
                        logger.info(f"    -> Descartando slice con cobertura {cov:.1f}% (< 1%)")
            else:
                # Lógica para un solo producto (mantiene compatibilidad)
                logger.info(f'  Fecha {date}: 1 producto')
                best = select_best_burst_for_aoi(date_products, aoi_wkt, min_coverage=1.0)
                if best:
                    selected_products.append(best)
        
        logger.info(f'  Productos seleccionados: {len(selected_products)}')
        products = selected_products
    
    # Agrupar por fecha/órbita para mosaicos (solo GRD)
    if create_mosaics:
        groups = group_products_by_date_orbit(products)
        logger.info(f"Agrupación para mosaicos:")
        logger.info(f"  Total de grupos (fecha+órbita): {len(groups)}")

        mosaic_count = 0
        single_count = 0

        for (date, orbit), group_products in groups.items():
            if len(group_products) > 1:
                mosaic_count += 1
                logger.info(f"  {date} órbita {orbit:03d}: {len(group_products)} productos → MOSAICO")
            else:
                single_count += 1

        logger.info(f"  Grupos con mosaico: {mosaic_count}")
        logger.info(f"  Productos individuales: {single_count}")
        logger.info("")

        # Procesar cada grupo
        processed = 0
        failed = 0

        for (date, orbit), group_products in groups.items():
            logger.info(f"[{date} órbita {orbit:03d}]")

            if len(group_products) > 1:
                # Crear mosaico
                result = create_mosaic(group_products, aoi_wkt, output_dir)
                if result:
                    processed += 1
                else:
                    # CORRECCIÓN: Procesar secuencialmente para evitar conflictos de memoria
                    # GPT ya paraleliza internamente con -c 4G
                    logger.info("  Procesando productos individualmente (secuencial para evitar OOM)...")

                    for i, product in enumerate(group_products, 1):
                        product_name = os.path.basename(product)[:60]
                        logger.info(f"    [{i}/{len(group_products)}] {product_name}...")
                        
                        result = subset_product(product, aoi_wkt, output_dir, insar_mode=insar_mode, subswath=subswath_from_dir)
                        if result:
                            processed += 1
                        else:
                            failed += 1
            else:
                # Producto individual
                result = subset_product(group_products[0], aoi_wkt, output_dir, insar_mode=insar_mode, subswath=subswath_from_dir)
                if result:
                    processed += 1
                else:
                    failed += 1

    else:
        # Procesar todos individualmente (SECUENCIAL para evitar conflictos de memoria)
        logger.info("Procesando productos individualmente (sin mosaicos)...")
        logger.info("⚙️  Modo secuencial: GPT ya paraleliza internamente")
        logger.info("   (evita Out-Of-Memory cuando múltiples GPT compiten por recursos)")
        logger.info("")

        processed = 0
        failed = 0

        # Procesar secuencialmente
        for i, product in enumerate(products, 1):
            product_name = os.path.basename(product)[:60]
            logger.info(f"[{i}/{len(products)}] {product_name}...")
            
            result = subset_product(product, aoi_wkt, output_dir, insar_mode=insar_mode, subswath=subswath_from_dir)
            if result:
                processed += 1
            else:
                failed += 1

    # Resumen
    logger.info("")
    logger.info("=" * 80)
    logger.info("RESUMEN PRE-PROCESAMIENTO")
    logger.info("=" * 80)
    logger.info(f"Productos procesados correctamente: {processed}")
    logger.info(f"Productos fallidos: {failed}")
    logger.info(f"Total: {len(products)}")
    logger.info(f"Productos pre-procesados guardados en: {output_dir}")
    logger.info("")


def main():
    parser = argparse.ArgumentParser(
        description='Pre-procesa productos Sentinel-1 con PyroSAR',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos de uso:

  # Pre-procesar productos SLC (modo SAR normal, con Deburst)
  python scripts/preprocess_products.py --slc

  # Pre-procesar productos SLC para InSAR (sin Deburst)
  python scripts/preprocess_products.py --slc --insar-mode

  # Pre-procesar ambos tipos
  python scripts/preprocess_products.py --slc
        """
    )

    parser.add_argument('--grd', action='store_true', help='Pre-procesar productos GRD')
    parser.add_argument('--slc', action='store_true', help='Pre-procesar productos SLC')
    parser.add_argument('--mosaic', action='store_true', help='Crear mosaicos de productos con misma fecha/órbita')
    parser.add_argument('--insar-mode', action='store_true', help='Modo InSAR para SLC (sin Deburst, mantiene estructura de bursts)')
    parser.add_argument('--config', type=str, default='config.txt', help='Archivo de configuración (default: config.txt)')

    args = parser.parse_args()

    # Si no se especifica ninguno, procesar ambos
    if not args.grd and not args.slc:
        args.grd = True
        args.slc = True

    logger.info("=" * 80)
    logger.info("PRE-PROCESAMIENTO SENTINEL-1")
    logger.info("=" * 80)
    if pyroSAR_available:
        logger.info(f"PyroSAR versión: {pyroSAR.__version__}")
    else:
        logger.info("PyroSAR: No disponible (usando solo SNAP/GPT)")
    logger.info("")

    # Cargar configuración
    config = load_config(args.config)
    aoi = config.get('AOI')

    logger.info(f"Configuración:")
    logger.info(f"  AOI: {aoi[:60]}...")
    logger.info(f"  Crear mosaicos: {'Sí' if args.mosaic else 'No'}")
    if args.slc:
        logger.info(f"  Modo InSAR (SLC sin Deburst): {'Sí' if args.insar_mode else 'No'}")
    logger.info("")

    # Pre-procesar GRD
    if args.grd:
        grd_dir = config.get('GRD_DIR', 'data/sentinel1_grd')
        grd_output = config.get('PREPROCESSED_GRD_DIR', 'data/preprocessed_grd')

        logger.info(f"  Directorio GRD entrada: {grd_dir}")
        logger.info(f"  Directorio GRD salida: {grd_output}")
        
        if os.path.isdir(grd_dir):
            preprocess_products(grd_dir, grd_output, aoi, product_type='GRD', create_mosaics=args.mosaic)
        else:
            logger.warning(f"Directorio GRD no existe: {grd_dir}")

    # Pre-procesar SLC
    if args.slc:
        slc_dir = config.get('SLC_DIR', 'data/sentinel1_slc')
        slc_output = config.get('PREPROCESSED_SLC_DIR', 'data/preprocessed_slc')

        logger.info(f"  Directorio SLC entrada: {slc_dir}")
        logger.info(f"  Directorio SLC salida: {slc_output}")
        
        if os.path.isdir(slc_dir):
            preprocess_products(slc_dir, slc_output, aoi, product_type='SLC', 
                              create_mosaics=args.mosaic, insar_mode=args.insar_mode)
        else:
            logger.warning(f"Directorio SLC no existe: {slc_dir}")

    logger.info("=" * 80)
    logger.info("¡PRE-PROCESAMIENTO COMPLETADO!")
    logger.info("=" * 80)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.warning("Interrumpido por el usuario")
        sys.exit(130)
    except Exception as e:
        logger.error(f"ERROR: {str(e)}", exc_info=True)
        sys.exit(1)
