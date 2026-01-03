#!/usr/bin/env python3
"""
burst_utils.py
Utilidades para fusión automática de bursts del mismo día

Este módulo proporciona funciones para detectar y fusionar automáticamente
bursts consecutivos del mismo pase orbital.
"""

import glob
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import List, Dict, Optional, Tuple
from pathlib import Path

# Importar utilidades comunes
sys.path.insert(0, os.path.dirname(__file__))
from processing_utils import (
    extract_date_from_filename,
    logger
)

try:
    from shapely.geometry import Polygon, box
    from shapely.wkt import loads as wkt_loads
    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False
    logger.warning("Shapely no disponible - selección por cobertura AOI deshabilitada")


def group_products_by_date(data_dir: str) -> Dict[str, List[str]]:
    """
    Agrupa productos .SAFE o .dim por fecha (YYYYMMDD)

    Args:
        data_dir: Directorio con productos .SAFE o .dim

    Returns:
        Dict con fecha como clave y lista de rutas como valor
    """
    if not os.path.exists(data_dir):
        logger.warning(f"Directorio no existe: {data_dir}")
        return {}

    # Buscar productos .SAFE primero
    pattern_safe = os.path.join(data_dir, '*.SAFE')
    safe_products = glob.glob(pattern_safe)

    # Si no hay .SAFE, buscar .dim (productos pre-procesados)
    if not safe_products:
        pattern_dim = os.path.join(data_dir, '*.dim')
        safe_products = glob.glob(pattern_dim)

    if not safe_products:
        return {}

    # Agrupar por fecha
    by_date = defaultdict(list)

    for safe_path in safe_products:
        basename = os.path.basename(safe_path)
        date_str = extract_date_from_filename(basename)

        if not date_str:
            continue

        # Usar solo YYYYMMDD (sin hora)
        date_key = date_str[:8]
        by_date[date_key].append(safe_path)

    # Ordenar bursts dentro de cada fecha por timestamp
    for date in by_date:
        by_date[date].sort(key=lambda x: extract_date_from_filename(os.path.basename(x)))

    return by_date


def create_slice_assembly_xml(input_products: List[str], output_path: str) -> str:
    """
    Crea XML para SliceAssembly (fusión de bursts consecutivos)

    Args:
        input_products: Lista de rutas a productos .SAFE
        output_path: Ruta de salida del producto fusionado

    Returns:
        String con el XML del workflow
    """
    # Crear nodos de lectura para cada producto
    read_nodes = []
    source_products = []

    for i, prod_path in enumerate(input_products):
        node_id = f"Read-{i+1}"
        read_nodes.append(f"""  <node id="{node_id}">
    <operator>Read</operator>
    <sources/>
    <parameters>
      <file>{prod_path}</file>
    </parameters>
  </node>""")

        if i == 0:
            source_products.append(f'      <sourceProduct refid="{node_id}"/>')
        else:
            source_products.append(f'      <sourceProduct.{i} refid="{node_id}"/>')

    read_nodes_str = '\n\n'.join(read_nodes)
    source_products_str = '\n'.join(source_products)

    xml = f"""<graph id="SliceAssembly">
  <version>1.0</version>

{read_nodes_str}

  <node id="SliceAssembly">
    <operator>SliceAssembly</operator>
    <sources>
{source_products_str}
    </sources>
    <parameters>
      <selectedPolarisations>VV</selectedPolarisations>
    </parameters>
  </node>

  <node id="Write">
    <operator>Write</operator>
    <sources>
      <sourceProduct refid="SliceAssembly"/>
    </sources>
    <parameters>
      <file>{output_path}</file>
      <formatName>BEAM-DIMAP</formatName>
    </parameters>
  </node>

</graph>"""

    return xml


def merge_bursts_if_needed(input_products: List[str], output_dir: str) -> Optional[str]:
    """
    Fusiona múltiples bursts en un solo producto usando SliceAssembly

    Args:
        input_products: Lista de rutas a productos .SAFE a fusionar
        output_dir: Directorio donde guardar el producto fusionado

    Returns:
        Ruta al producto fusionado (.SAFE) o None si falla
    """
    if len(input_products) < 2:
        # No hay nada que fusionar
        return None

    # Extraer fecha del primer burst
    first_burst = os.path.basename(input_products[0])
    date_str = extract_date_from_filename(first_burst)
    if not date_str:
        logger.error("No se pudo extraer fecha del burst")
        return None

    date_key = date_str[:8]
    satellite = first_burst[:3]  # S1A, S1B, S1C

    # Determinar tipo de producto (SLC o GRD)
    if '_SLC_' in first_burst or '_SLC__' in first_burst:
        product_type = 'SLC'
    elif '_GRD' in first_burst:
        product_type = 'GRD'
    else:
        product_type = 'UNKNOWN'

    # Nombre del producto fusionado (.dim para BEAM-DIMAP)
    output_name = f"{satellite}_IW_{product_type}_MERGED_{date_key}.dim"
    output_path = os.path.join(output_dir, output_name)

    # Verificar si ya existe
    if os.path.exists(output_path):
        logger.info(f"   ✓ Producto fusionado ya existe: {output_name}")
        return output_path

    try:
        # Crear XML
        xml = create_slice_assembly_xml(input_products, output_path)

        # Guardar XML temporal
        with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as tf:
            tf.write(xml)
            xml_file = tf.name

        try:
            # Ejecutar GPT
            logger.info(f"   🔄 Fusionando {len(input_products)} bursts del {date_key}...")

            result = subprocess.run(
                ['gpt', xml_file, '-c', '16G', '-q', '4'],
                capture_output=True,
                text=True,
                timeout=3600  # 60 min timeout (fusión es lenta)
            )

            if result.returncode == 0 and os.path.exists(output_path):
                logger.info(f"   ✅ Fusión exitosa: {output_name}")
                return output_path
            else:
                logger.error(f"   ✗ Error en fusión (exit code {result.returncode})")
                if result.stderr:
                    # Mostrar últimas líneas del error
                    stderr_lines = result.stderr.split('\n')
                    for line in stderr_lines[-10:]:
                        if line.strip():
                            logger.error(f"     {line}")
                return None

        finally:
            # Limpiar XML temporal
            if os.path.exists(xml_file):
                os.unlink(xml_file)

    except subprocess.TimeoutExpired:
        logger.error("   ✗ Timeout ejecutando GPT")
        return None
    except Exception as e:
        logger.error(f"   ✗ Error en fusión: {e}")
        return None


def select_representative_bursts(data_dir: str) -> List[str]:
    """
    Retorna TODOS los productos descargados para procesamiento InSAR

    CRÍTICO: Para InSAR con productos originales (.SAFE):
    - NO usar SliceAssembly (incompatible con TOPSAR-Split)
    - Procesar TODOS los slices del mismo día para cobertura completa
    - Si hay múltiples productos de la misma fecha = frontera azimutal
    
    IMPORTANTE: Múltiples slices del mismo día deben procesarse TODOS porque:
    - Cubren diferentes partes del AOI (norte/sur)
    - Descartar uno crea puntos ciegos en el mapa de coherencia
    - Para detección de fugas, necesitamos cobertura 100%

    Args:
        data_dir: Directorio con productos .SAFE o .dim

    Returns:
        Lista de TODOS los productos (múltiples por fecha si existen)
    """
    logger.info("=" * 80)
    logger.info("SELECCIÓN DE PRODUCTOS PARA InSAR")
    logger.info("=" * 80)

    # Agrupar por fecha
    by_date = group_products_by_date(data_dir)

    if not by_date:
        logger.warning("No se encontraron productos")
        return []

    multi_slice_dates = sum(1 for bursts in by_date.values() if len(bursts) > 1)
    
    logger.info(f"\n📊 Análisis:")
    logger.info(f"   Total fechas: {len(by_date)}")
    logger.info(f"   Fechas con múltiples slices: {multi_slice_dates}")

    # Procesar TODOS los productos (no seleccionar)
    selected_products = []

    for date in sorted(by_date.keys()):
        bursts = by_date[date]
        
        if len(bursts) > 1:
            logger.info(f"\n   Fecha {date}: {len(bursts)} slices (frontera azimutal)")
            logger.info(f"      → Procesando TODOS para cobertura completa")
            for burst in bursts:
                basename = os.path.basename(burst)
                time_str = extract_date_from_filename(basename)
                if time_str and len(time_str) >= 15:
                    logger.info(f"         ✓ {basename[:70]}... ({time_str[9:15]})")
                else:
                    logger.info(f"         ✓ {basename[:70]}...")
                selected_products.append(burst)
        else:
            logger.info(f"   Fecha {date}: 1 producto → {os.path.basename(bursts[0])[:70]}...")
            selected_products.append(bursts[0])

    logger.info("=" * 80)
    logger.info(f"PRODUCTOS TOTALES: {len(selected_products)}")
    logger.info("=" * 80)

    return selected_products


def get_product_footprint(product_path: str) -> Optional[Polygon]:
    """
    Extrae el footprint geográfico de un producto .SAFE
    
    Args:
        product_path: Ruta al producto .SAFE
    
    Returns:
        Polygon de Shapely o None si no se puede extraer
    """
    if not SHAPELY_AVAILABLE:
        return None
    
    try:
        # Buscar manifest.safe
        manifest = os.path.join(product_path, 'manifest.safe')
        if not os.path.exists(manifest):
            return None
        
        # Parsear XML
        tree = ET.parse(manifest)
        root = tree.getroot()
        
        # Buscar coordenadas del footprint
        # Namespace de Sentinel-1
        ns = {
            'safe': 'http://www.esa.int/safe/sentinel-1.0',
            'gml': 'http://www.opengis.net/gml'
        }
        
        # Buscar footprint en metadataSection
        coords_elem = root.find('.//gml:coordinates', ns)
        if coords_elem is None:
            return None
        
        # Parsear coordenadas (formato: "lat1,lon1 lat2,lon2 ...")
        coords_text = coords_elem.text.strip()
        points = []
        for coord_pair in coords_text.split():
            lat, lon = map(float, coord_pair.split(','))
            points.append((lon, lat))  # Shapely usa (lon, lat)
        
        if len(points) < 3:
            return None
        
        return Polygon(points)
    
    except Exception as e:
        logger.debug(f"Error extrayendo footprint: {e}")
        return None


def calculate_aoi_coverage(products: List[str], aoi_wkt: str) -> Dict[str, float]:
    """
    Calcula el porcentaje de cobertura del AOI para cada producto
    
    Args:
        products: Lista de rutas a productos .SAFE
        aoi_wkt: WKT del AOI
    
    Returns:
        Dict con {product_path: coverage_percentage}
    """
    if not SHAPELY_AVAILABLE:
        return {p: 0.0 for p in products}
    
    try:
        aoi_geom = wkt_loads(aoi_wkt)
        aoi_area = aoi_geom.area
        
        coverage = {}
        for product in products:
            footprint = get_product_footprint(product)
            if footprint is None:
                coverage[product] = 0.0
                continue
            
            # Calcular intersección
            try:
                intersection = footprint.intersection(aoi_geom)
                coverage_pct = (intersection.area / aoi_area) * 100.0
                coverage[product] = coverage_pct
            except Exception:
                coverage[product] = 0.0
        
        return coverage
    
    except Exception as e:
        logger.warning(f"Error calculando cobertura: {e}")
        return {p: 0.0 for p in products}


def select_slices_for_grd(products: List[str], aoi_wkt: Optional[str] = None) -> List[Tuple[str, int]]:
    """
    Selecciona qué slices procesar para GRD basándose en cobertura del AOI
    
    ESTRATEGIA:
    1. Si AOI disponible: calcular cobertura de cada slice
    2. Si un slice cubre >95% del AOI: usar solo ese
    3. Si necesitamos múltiples slices: retornar todos los que aporten cobertura
    4. Sin AOI: retornar solo el slice del medio (mejor compromiso)
    
    Args:
        products: Lista de productos .SAFE del mismo día (ordenados por hora)
        aoi_wkt: WKT del AOI (opcional)
    
    Returns:
        Lista de tuplas (product_path, part_number) donde part_number indica
        si es parte 1, 2, etc. (0 si es único)
    """
    if not products:
        return []
    
    if len(products) == 1:
        return [(products[0], 0)]
    
    # Sin AOI: seleccionar slice del medio
    if not aoi_wkt or not SHAPELY_AVAILABLE:
        middle_idx = len(products) // 2
        logger.info(f"   Sin AOI - seleccionando slice {middle_idx+1}/{len(products)}")
        return [(products[middle_idx], 0)]
    
    # Calcular cobertura
    coverage = calculate_aoi_coverage(products, aoi_wkt)
    
    # Ordenar por cobertura
    sorted_products = sorted(coverage.items(), key=lambda x: x[1], reverse=True)
    
    # Verificar si un solo slice cubre casi todo el AOI
    best_product, best_coverage = sorted_products[0]
    
    if best_coverage >= 95.0:
        logger.info(f"   ✓ Slice único con {best_coverage:.1f}% cobertura AOI")
        return [(best_product, 0)]
    
    # Necesitamos múltiples slices
    logger.info(f"   ⚠️  Ningún slice cubre >95% del AOI (mejor: {best_coverage:.1f}%)")
    logger.info(f"   → Procesando múltiples slices para cobertura completa")
    
    # Seleccionar slices que aporten cobertura significativa (>10%)
    selected = []
    part_num = 1
    
    for product, cov in sorted_products:
        if cov >= 10.0:  # Threshold mínimo de cobertura
            selected.append((product, part_num))
            logger.info(f"      Part {part_num}: {os.path.basename(product)[:50]}... ({cov:.1f}% AOI)")
            part_num += 1
    
    return selected if selected else [(products[0], 0)]


# DEPRECATED: Esta función ya no se usa (procesar TODOS los slices, no seleccionar)
# Se mantiene comentada por si se necesita restaurar en el futuro
#
# def select_best_coverage_product(products: List[str]) -> Optional[str]:
#     """
#     [DEPRECATED] Seleccionaba el producto con mejor cobertura de IW1 de una lista.
#     
#     PROBLEMA: Causaba puntos ciegos al descartar slices que cubrían otras partes del AOI.
#     SOLUCIÓN: Ahora se procesan TODOS los productos (ver auto_merge_bursts)
#     """
#     pass


def filter_by_orbit(products: List[str], target_orbit: str = 'DESCENDING') -> List[str]:
    """
    Filtra productos por tipo de órbita (ASCENDING/DESCENDING)
    
    Lógica horaria para España/Europa:
    - DESCENDING: 04:00-08:00 UTC (mañanas ~06:00)
    - ASCENDING: 16:00-20:00 UTC (tardes ~17:00-18:00)
    
    Args:
        products: Lista de paths a productos .SAFE
        target_orbit: 'DESCENDING' o 'ASCENDING'
    
    Returns:
        Lista filtrada de productos que coinciden con la órbita objetivo
    """
    filtered = []
    
    for product_path in products:
        basename = os.path.basename(product_path)
        
        try:
            # Formato: S1X_IW_GRDH_1SDV_YYYYMMDDTHHMMSS_...
            time_part = basename.split('_')[4]  # YYYYMMDDTHHMMSS
            hour = int(time_part[9:11])  # Extraer HH
            
            # Determinar tipo de órbita según hora
            is_descending = (4 <= hour <= 8)
            is_ascending = (16 <= hour <= 20)
            
            # Filtrar según objetivo
            if target_orbit == 'DESCENDING' and is_descending:
                filtered.append(product_path)
            elif target_orbit == 'ASCENDING' and is_ascending:
                filtered.append(product_path)
            # Si no coincide, se descarta
            
        except (IndexError, ValueError):
            # Si no se puede determinar hora, incluir por defecto
            logger.warning(f"No se pudo determinar órbita de: {basename[:60]}...")
            filtered.append(product_path)
    
    return filtered


def auto_select_grd_products(data_dir: str, aoi_wkt: Optional[str] = None, target_orbit: str = 'DESCENDING') -> List[Tuple[str, str]]:
    """
    Selecciona productos GRD óptimos basándose en cobertura del AOI y tipo de órbita
    
    DIFERENCIA con auto_merge_bursts (para InSAR):
    - InSAR: procesa TODOS los slices (coherencia necesita cobertura completa)
    - GRD: procesa solo los slices necesarios para cubrir el AOI
    
    ESTRATEGIA:
    1. Agrupar productos por fecha
    2. FILTRAR por órbita (DESCENDING/ASCENDING)
    3. Para cada fecha:
       - Si un slice cubre >95% AOI → procesar solo ese
       - Si necesitamos varios slices → procesar todos con part1, part2, etc.
       - Sin AOI → seleccionar slice del medio
    
    Args:
        data_dir: Directorio con productos .SAFE
        aoi_wkt: WKT del AOI (opcional)
        target_orbit: 'DESCENDING' o 'ASCENDING' (default: DESCENDING)
    
    Returns:
        Lista de tuplas (product_path, output_name) donde output_name incluye
        sufijo _partN si hay múltiples slices del mismo día
    """
    logger.info("=" * 80)
    logger.info("SELECCIÓN DE PRODUCTOS GRD (cobertura AOI + órbita)")
    logger.info("=" * 80)
    
    # Agrupar por fecha
    by_date = group_products_by_date(data_dir)
    
    if not by_date:
        logger.warning("No se encontraron productos")
        return []
    
    # FILTRAR POR ÓRBITA antes de procesar
    logger.info(f"\n🛰️  Filtro de órbita: {target_orbit}")
    
    total_before = sum(len(slices) for slices in by_date.values())
    by_date_filtered = {}
    discarded_count = 0
    
    for date, slices in by_date.items():
        filtered_slices = filter_by_orbit(slices, target_orbit)
        
        if filtered_slices:
            by_date_filtered[date] = filtered_slices
        
        discarded = len(slices) - len(filtered_slices)
        if discarded > 0:
            discarded_count += discarded
            orbit_type = "ASCENDING" if target_orbit == "DESCENDING" else "DESCENDING"
            logger.info(f"   Fecha {date}: {discarded} producto(s) {orbit_type} descartado(s)")
    
    logger.info(f"   Total productos antes: {total_before}")
    logger.info(f"   Total productos después: {sum(len(s) for s in by_date_filtered.values())}")
    logger.info(f"   Descartados: {discarded_count}")
    
    # Usar productos filtrados
    by_date = by_date_filtered
    
    if not by_date:
        logger.warning("No quedan productos después del filtro de órbita")
        return []
    
    multi_slice_dates = sum(1 for bursts in by_date.values() if len(bursts) > 1)
    
    logger.info(f"\n📊 Análisis (después de filtro):")
    logger.info(f"   Total fechas: {len(by_date)}")
    logger.info(f"   Fechas con múltiples slices: {multi_slice_dates}")
    
    if aoi_wkt and SHAPELY_AVAILABLE:
        logger.info(f"   Modo: Selección inteligente (cobertura AOI)")
    else:
        logger.info(f"   Modo: Selección por defecto (slice central)")
    
    # Procesar cada fecha
    selected_products = []
    
    for date in sorted(by_date.keys()):
        slices = by_date[date]
        
        if len(slices) == 1:
            # Un solo slice
            date_str = extract_date_from_filename(os.path.basename(slices[0]))
            output_name = f"GRD_{date_str[:8]}" if date_str else f"GRD_{date}"
            selected_products.append((slices[0], output_name))
            logger.info(f"\n   Fecha {date}: 1 slice → {output_name}")
        else:
            # Múltiples slices - aplicar selección inteligente
            logger.info(f"\n   Fecha {date}: {len(slices)} slices disponibles")
            
            selected_slices = select_slices_for_grd(slices, aoi_wkt)
            
            for product_path, part_num in selected_slices:
                date_str = extract_date_from_filename(os.path.basename(product_path))
                
                if part_num == 0:
                    # Slice único seleccionado
                    output_name = f"GRD_{date_str[:8]}" if date_str else f"GRD_{date}"
                else:
                    # Múltiples partes
                    output_name = f"GRD_{date_str[:8]}_part{part_num}" if date_str else f"GRD_{date}_part{part_num}"
                
                selected_products.append((product_path, output_name))
    
    logger.info("\n" + "=" * 80)
    logger.info(f"PRODUCTOS SELECCIONADOS: {len(selected_products)}")
    logger.info("=" * 80)
    
    return selected_products


def auto_merge_bursts(data_dir: str) -> List[str]:
    """
    Retorna TODOS los productos descargados (múltiples slices por fecha si existen)

    IMPORTANTE: Si hay múltiples productos de la misma fecha, significa que el AOI
    está en una zona de frontera azimutal (entre slices consecutivos).
    
    Procesar TODOS los slices es crítico para:
    - Evitar puntos ciegos en detección de fugas
    - Garantizar cobertura 100% del AOI en dirección longitudinal
    - No descartar datos que cubren diferentes partes del área de interés

    Esta función:
    1. Agrupa productos por fecha (YYYYMMDD)
    2. Retorna TODOS los productos (múltiples slices si existen)
    3. El procesamiento posterior manejará múltiples imágenes del mismo día

    Args:
        data_dir: Directorio con productos .SAFE

    Returns:
        Lista de rutas a productos .SAFE (TODOS los descargados)
    """
    logger.info("=" * 80)
    logger.info("ANÁLISIS DE PRODUCTOS DESCARGADOS")
    logger.info("=" * 80)

    # Agrupar por fecha
    by_date = group_products_by_date(data_dir)

    if not by_date:
        logger.warning("No se encontraron productos")
        return []

    # Separar grupos
    multi_burst_dates = {date: bursts for date, bursts in by_date.items() if len(bursts) > 1}
    single_burst_dates = {date: bursts for date, bursts in by_date.items() if len(bursts) == 1}

    logger.info(f"\n📊 Análisis:")
    logger.info(f"   Fechas con 1 producto: {len(single_burst_dates)}")
    logger.info(f"   Fechas con múltiples productos (slices): {len(multi_burst_dates)}")
    logger.info(f"   Total fechas: {len(by_date)}")

    # Lista de productos finales
    final_products = []

    # Añadir productos individuales
    for date, bursts in single_burst_dates.items():
        final_products.extend(bursts)

    # CRÍTICO: Procesar TODOS los slices (no seleccionar "el mejor")
    if multi_burst_dates:
        logger.info(f"\n🔍 Detectados múltiples slices para {len(multi_burst_dates)} fechas")
        logger.info(f"   → Procesando TODOS los slices para cobertura completa del AOI")

        for date in sorted(multi_burst_dates.keys()):
            bursts = multi_burst_dates[date]
            logger.info(f"\n   Fecha {date}: {len(bursts)} slices (frontera azimutal)")
            
            # Añadir TODOS los slices
            for burst in bursts:
                basename = os.path.basename(burst)
                time_str = extract_date_from_filename(basename)
                if time_str and len(time_str) >= 15:
                    logger.info(f"      ✓ {basename[:70]}... ({time_str[9:15]})")
                else:
                    logger.info(f"      ✓ {basename[:70]}...")
                final_products.append(burst)

        logger.info(f"\n✅ Todos los slices se procesarán para garantizar cobertura 100%")
    else:
        logger.info("\n✓ Todas las fechas tienen un solo producto")

    logger.info("=" * 80)
    logger.info(f"PRODUCTOS TOTALES: {len(final_products)}")
    logger.info("=" * 80)

    # Ordenar por fecha y hora
    final_products.sort(key=lambda x: extract_date_from_filename(os.path.basename(x)) or '')

    return final_products
