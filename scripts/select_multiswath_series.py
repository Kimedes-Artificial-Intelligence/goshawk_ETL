#!/usr/bin/env python3
"""
Script para seleccionar múltiples series InSAR por sub-swath.

En lugar de seleccionar solo el sub-swath óptimo, este script genera
TODAS las series posibles, permitiendo procesamiento independiente de cada una.

Estrategia:
- Para cada sub-swath que tenga cobertura del AOI, genera una serie independiente
- Selecciona el mejor producto (burst) para cada fecha en cada serie
- Permite análisis comparativo y validación cruzada
- NUEVO: Puede consultar API de Copernicus para incluir productos no descargados
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


# Importar funciones del script original
sys.path.insert(0, os.path.dirname(__file__))
from select_optimal_subswath import (
    analyze_slc_products
)
from logging_utils import LoggerConfig
import logging

# Logger global - se configurará en main()
logger = None


def fetch_copernicus_products(auth, aoi_bbox, start_date, end_date, orbit_direction, satellites_filter=None):
    """
    Consulta la API de Copernicus para obtener productos disponibles.
    
    Args:
        auth: Objeto CopernicusAuth
        aoi_bbox: Bounding box del AOI
        start_date: Fecha de inicio
        end_date: Fecha de fin
        orbit_direction: Dirección de órbita
        satellites_filter: Lista de satélites a filtrar (ej: ['S1A', 'S1C'])
    
    Returns:
        Lista de productos disponibles en Copernicus
    """
    from download_copernicus import search_products, parse_product_name
    
    logger.info("\n" + "=" * 80)
    logger.info("CONSULTANDO API DE COPERNICUS")
    logger.info("=" * 80)
    
    # Buscar productos en Copernicus
    products = search_products(
        auth=auth,
        collection="SENTINEL-1",
        product_type="SLC",
        start_date=start_date,
        end_date=end_date,
        bbox=aoi_bbox,
        orbit_direction=orbit_direction,
        aoi_name=aoi_bbox.get('aoi_name')
    )
    
    if not products:
        logger.info("  No se encontraron productos en Copernicus")
        return []
    
    # Filtrar por satélite si se especifica
    if satellites_filter:
        original_count = len(products)
        products = [p for p in products if parse_product_name(p['Name'])['satellite'] in satellites_filter]
        logger.info(f"  Filtrados por satélite: {len(products)}/{original_count} productos")
    
    logger.info(f"  ✓ Encontrados {len(products)} productos en Copernicus")
    return products


def merge_local_and_copernicus_products(local_analysis, copernicus_products, slc_dir, repository=None):
    """
    Combina productos locales con productos disponibles en Copernicus.
    
    Args:
        local_analysis: Análisis de productos locales
        copernicus_products: Lista de productos de Copernicus
        slc_dir: Directorio de SLCs locales
        repository: InSARRepository para verificar productos procesados (opcional)
    
    Returns:
        analysis actualizado con todos los productos (locales + disponibles)
    """
    from download_copernicus import parse_product_name
    
    logger.info("\n" + "=" * 80)
    logger.info("COMBINANDO PRODUCTOS LOCALES Y DISPONIBLES")
    logger.info("=" * 80)
    
    # Crear índice de productos locales por fecha
    local_dates = set()
    for date in local_analysis['products_by_date'].keys():
        if isinstance(date, str):
            local_dates.add(date)
        else:
            local_dates.add(date.strftime('%Y%m%d'))
    
    logger.info(f"  Productos locales: {len(local_dates)} fechas")
    
    # Añadir productos de Copernicus que no están localmente
    added_count = 0
    processed_count = 0
    
    for cop_product in copernicus_products:
        product_name = cop_product['Name']
        info = parse_product_name(product_name)
        date_str = info['date_str']  # YYYY-MM-DD
        date_yyyymmdd = date_str.replace('-', '')  # YYYYMMDD
        
        # Verificar si está descargado
        local_path = Path(slc_dir) / product_name
        is_downloaded = local_path.exists()
        
        # Verificar si está procesado (si se proporciona repository)
        is_processed = False
        if repository and not is_downloaded:
            from download_copernicus import is_slc_fully_processed
            is_processed = is_slc_fully_processed(product_name, repository)
        
        if is_downloaded:
            # Ya está en local_analysis, skip
            continue
        elif is_processed:
            processed_count += 1
            logger.info(f"  ~ {date_str} - {product_name[:50]}... (procesado)")
            continue
        else:
            # Producto disponible pero no descargado
            added_count += 1
            
            # Crear entrada "virtual" para este producto
            # No sabemos exactamente qué subswaths cubre sin descargarlo,
            # pero podemos crear un placeholder
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            
            if date_obj not in local_analysis['products_by_date']:
                local_analysis['products_by_date'][date_obj] = []
            
            # Añadir como producto disponible
            local_analysis['products_by_date'][date_obj].append({
                'product': product_name,
                'path': str(local_path),  # Path donde DEBERÍA estar
                'subswaths_available': ['IW1', 'IW2', 'IW3'],  # Asumir todos disponibles
                'subswaths_covering_aoi': ['IW1', 'IW2', 'IW3'],  # Asumir todos cubren (conservador)
                'subswath_coverage': {'IW1': True, 'IW2': True, 'IW3': True},
                'status': 'available',  # Marcar como disponible pero no descargado
                'copernicus_id': cop_product['Id']
            })
            
            logger.info(f"  + {date_str} - {product_name[:50]}... (disponible)")
    
    logger.info(f"  ✓ Añadidos: {added_count} productos disponibles")
    if processed_count > 0:
        logger.info(f"  ✓ Omitidos: {processed_count} productos ya procesados")
    logger.info(f"  Total fechas: {len(local_analysis['products_by_date'])}")
    
    return local_analysis


def group_products_by_series(analysis):
    """
    Agrupa productos en series independientes por sub-swath.

    Para cada sub-swath que tenga cobertura del AOI, crea una serie con:
    - El mejor producto para cada fecha
    - Información de cobertura
    - Estado de descarga (descargado/disponible/procesado)

    Returns:
        dict: {subswath: {'products': [...], 'dates': [...], 'coverage': int}}
    """
    products_by_date = analysis['products_by_date']

    # Identificar qué sub-swaths tienen cobertura
    subswaths_with_coverage = set()
    for date, products in products_by_date.items():
        for product in products:
            subswaths_with_coverage.update(product['subswaths_covering_aoi'])

    # FILTRAR: Solo procesar IW1 e IW2 (excluir IW3 por sombras urbanas)
    subswaths_with_coverage = {sw for sw in subswaths_with_coverage if sw in ['IW1', 'IW2']}

    if not subswaths_with_coverage:
        logger.info("⚠️  Ningún sub-swath IW1/IW2 tiene cobertura del AOI")
        logger.info("    (IW3 excluido automáticamente por sombras en terreno urbano)")
        return {}

    logger.info("\n" + "=" * 80)
    logger.info("AGRUPACIÓN DE PRODUCTOS EN SERIES POR SUB-SWATH")
    logger.info("=" * 80)
    logger.info(f"Sub-swaths con cobertura del AOI: {sorted(subswaths_with_coverage)}")
    logger.info(f"Nota: IW3 excluido automáticamente (alta distorsión/sombras en terreno urbano)")

    # Crear series para cada sub-swath
    series = {}

    for subswath in sorted(subswaths_with_coverage):
        series[subswath] = {
            'products': [],
            'dates': [],
            'dates_without_coverage': [],
            'coverage': 0
        }

        logger.info(f"\n--- Serie {subswath} ---")

        for date in sorted(products_by_date.keys()):
            products = products_by_date[date]

            # Buscar productos que cubran el AOI con este sub-swath
            candidates = [p for p in products if subswath in p['subswaths_covering_aoi']]

            if candidates:
                # Seleccionar el primer producto (podríamos añadir criterios de selección)
                selected = candidates[0]
                
                status = selected.get('status', 'downloaded')
                status_icon = '✓' if status == 'downloaded' else '○'

                product_entry = {
                    'date': date.strftime('%Y-%m-%d'),
                    'product': selected['product'],
                    'path': selected['path'],
                    'subswath': subswath,
                    'all_subswaths_available': sorted(selected['subswaths_available']),
                    'all_subswaths_covering_aoi': sorted(selected['subswaths_covering_aoi']),
                    'status': status
                }
                
                # Si es de Copernicus, añadir ID
                if 'copernicus_id' in selected:
                    product_entry['copernicus_id'] = selected['copernicus_id']
                
                series[subswath]['products'].append(product_entry)
                series[subswath]['dates'].append(date)
                series[subswath]['coverage'] += 1

                logger.info(f"  {status_icon} {date} - {selected['product']}")
            else:
                series[subswath]['dates_without_coverage'].append(date)
                logger.info(f"  ✗ {date} - No hay cobertura en {subswath}")

    return series


def print_series_summary(series):
    """Imprime resumen de todas las series."""
    logger.info("\n" + "=" * 80)
    logger.info("RESUMEN DE SERIES")
    logger.info("=" * 80)

    for subswath in sorted(series.keys()):
        s = series[subswath]
        logger.info(f"\n{subswath}:")
        logger.info(f"  Productos seleccionados: {s['coverage']}")
        logger.info(f"  Fechas sin cobertura: {len(s['dates_without_coverage'])}")

        if s['dates']:
            logger.info(f"  Rango temporal: {min(s['dates'])} → {max(s['dates'])}")

            # Calcular intervalo temporal promedio
            if len(s['dates']) > 1:
                sorted_dates = sorted(s['dates'])
                intervals = [(sorted_dates[i+1] - sorted_dates[i]).days
                           for i in range(len(sorted_dates)-1)]
                avg_interval = sum(intervals) / len(intervals)
                logger.info(f"  Intervalo temporal promedio: {avg_interval:.1f} días")


def save_series(series, aoi_bbox, orbit_direction, base_output_path):
    """
    Guarda cada serie en un archivo JSON independiente.

    Estructura de archivos:
    - selected_products_{orbit}_{subswath}.json

    Args:
        series: Diccionario con las series por sub-swath
        aoi_bbox: Bounding box del AOI
        orbit_direction: 'DESCENDING' o 'ASCENDING'
        base_output_path: Directorio base para guardar archivos
    """
    base_path = Path(base_output_path)
    saved_files = []

    logger.info("\n" + "=" * 80)
    logger.info("GUARDANDO SERIES")
    logger.info("=" * 80)

    for subswath in sorted(series.keys()):
        s = series[subswath]

        # Nombre del archivo
        orbit_short = orbit_direction.lower()[:4]  # 'desc' o 'asce'
        filename = f"selected_products_{orbit_short}_{subswath.lower()}.json"
        output_file = base_path / filename

        # Datos a guardar
        output_data = {
            'orbit_direction': orbit_direction,
            'subswath': subswath,
            'total_products': s['coverage'],
            'aoi_bbox': aoi_bbox,
            'products': s['products'],
            'dates_without_coverage': [d.strftime('%Y-%m-%d') for d in s['dates_without_coverage']],
            'analysis_date': datetime.now().isoformat()
        }

        # Guardar
        with open(output_file, 'w') as f:
            json.dump(output_data, f, indent=2)

        logger.info(f"✓ {subswath}: {output_file.name} ({s['coverage']} productos)")
        saved_files.append(output_file)

    return saved_files


def create_processing_manifest(series, aoi_bbox, orbit_direction, output_path):
    """
    Crea un archivo manifest global con todas las series para facilitar el procesamiento.

    Este archivo sirve como índice para saber qué series procesar.
    """
    manifest = {
        'orbit_direction': orbit_direction,
        'total_series': len(series),
        'aoi_bbox': aoi_bbox,
        'series': {},
        'created_at': datetime.now().isoformat()
    }

    for subswath in sorted(series.keys()):
        s = series[subswath]
        orbit_short = orbit_direction.lower()[:4]

        manifest['series'][subswath] = {
            'subswath': subswath,
            'total_products': s['coverage'],
            'date_range': {
                'start': min(s['dates']).strftime('%Y-%m-%d') if s['dates'] else None,
                'end': max(s['dates']).strftime('%Y-%m-%d') if s['dates'] else None
            },
            'config_file': f"selected_products_{orbit_short}_{subswath.lower()}.json",
            'processing_dir': f"insar_{orbit_short}_{subswath.lower()}"
        }

    manifest_file = Path(output_path) / f"processing_manifest_{orbit_direction.lower()}.json"

    with open(manifest_file, 'w') as f:
        json.dump(manifest, f, indent=2)

    logger.info(f"\n✓ Manifest creado: {manifest_file.name}")
    return manifest_file


def main():
    import argparse
    
    # Configuración de argumentos
    parser = argparse.ArgumentParser(
        description='Selección de series múltiples por sub-swath'
    )
    parser.add_argument('--data-dir', type=str, required=True,
                        help='Directorio con productos SLC')
    parser.add_argument('--aoi-geojson', type=str, required=True,
                        help='Archivo GeoJSON con el AOI')
    parser.add_argument('--output-dir', type=str, required=True,
                        help='Directorio de salida')
    parser.add_argument('--orbit-direction', type=str, default='DESCENDING',
                        choices=['DESCENDING', 'ASCENDING'],
                        help='Dirección de órbita')
    parser.add_argument('--start-date', type=str,
                        help='Fecha inicio del período (YYYY-MM-DD)')
    parser.add_argument('--end-date', type=str,
                        help='Fecha fin del período (YYYY-MM-DD)')
    parser.add_argument('--satellites', type=str, nargs='+',
                        help='Satélites a filtrar (ej: S1A S1C)')
    parser.add_argument('--use-copernicus-api', action='store_true',
                        help='Consultar API de Copernicus para incluir productos no descargados')
    parser.add_argument('--check-processed', action='store_true',
                        help='Verificar productos ya procesados en el repositorio')
    parser.add_argument('--repo-dir', default='data/processed_products',
                        help='Directorio del repositorio de productos procesados')
    parser.add_argument('--log-dir', default='logs',
                        help='Directorio donde guardar logs (default: logs/)')

    args = parser.parse_args()

    # Configurar logger con directorio especificado
    global logger
    logger = LoggerConfig.setup_script_logger(
        script_name="select_multiswath_series",
        log_dir=args.log_dir,
        level=logging.INFO
    )
    
    slc_dir = Path(args.data_dir)
    aoi_geojson = Path(args.aoi_geojson)
    output_dir = Path(args.output_dir)
    orbit_direction = args.orbit_direction.upper()
    satellites_filter = args.satellites  # Puede ser None
    
    # Parsear fechas si se proporcionaron
    start_date = None
    end_date = None
    if args.start_date:
        try:
            start_date = datetime.strptime(args.start_date, '%Y-%m-%d')
        except ValueError:
            logger.info(f"Error: Formato de fecha inicio inválido: {args.start_date}")
            return 1
    if args.end_date:
        try:
            end_date = datetime.strptime(args.end_date, '%Y-%m-%d')
        except ValueError:
            logger.info(f"Error: Formato de fecha fin inválido: {args.end_date}")
            return 1
    
    # Si no se especificaron fechas y se usa API, usar últimos 6 meses
    if args.use_copernicus_api and not (start_date and end_date):
        end_date = datetime.now()
        start_date = end_date - timedelta(days=180)
        logger.info(f"⚠️  No se especificaron fechas, usando últimos 6 meses: {start_date.date()} a {end_date.date()}")

    logger.info("=" * 80)
    logger.info("SELECCIÓN DE SERIES MÚLTIPLES POR SUB-SWATH")
    logger.info("=" * 80)
    logger.info(f"Directorio SLC: {slc_dir}")
    logger.info(f"AOI GeoJSON: {aoi_geojson}")
    logger.info(f"Directorio de salida: {output_dir}")
    logger.info(f"Dirección de órbita: {orbit_direction}")
    if args.use_copernicus_api:
        logger.info(f"⚠️  Modo: Consultar API de Copernicus")
    if start_date and end_date:
        logger.info(f"Período: {args.start_date if args.start_date else start_date.date()} a {args.end_date if args.end_date else end_date.date()}")
    if satellites_filter:
        logger.info(f"Satélites: {', '.join(satellites_filter)}")
    logger.info("")  # Línea en blanco

    # Verificar que el AOI existe
    if not aoi_geojson.exists():
        logger.info(f"Error: No existe el archivo AOI: {aoi_geojson}")
        return 1

    # Analizar productos locales SIN mostrar detalles (verbose=False)
    logger.info("Analizando productos SLC locales...")
    analysis = analyze_slc_products(str(slc_dir), str(aoi_geojson), verbose=False)

    if not analysis or not analysis['products_by_date']:
        logger.info("⚠️  No se encontraron productos SLC locales")
        if not args.use_copernicus_api:
            logger.info("Error: No hay productos para procesar. Usa --use-copernicus-api para consultar disponibles.")
            return 1
        # Crear análisis vacío para combinar con Copernicus
        from aoi_utils import geojson_to_bbox
        aoi_bbox = geojson_to_bbox(str(aoi_geojson))
        analysis = {
            'products_by_date': {},
            'aoi_bbox': aoi_bbox
        }
    
    # Si se solicita consultar Copernicus API
    copernicus_products = []
    if args.use_copernicus_api:
        # Obtener credenciales
        from download_copernicus import CopernicusAuth
        from dotenv import load_dotenv
        load_dotenv()
        
        username = os.environ.get('COPERNICUS_USER')
        password = os.environ.get('COPERNICUS_PASSWORD')
        
        if not username or not password:
            logger.info("Error: Credenciales de Copernicus no encontradas")
            logger.info("Configura COPERNICUS_USER y COPERNICUS_PASSWORD en .env")
            return 1
        
        auth = CopernicusAuth(username, password)
        
        # Consultar productos disponibles
        copernicus_products = fetch_copernicus_products(
            auth, 
            analysis['aoi_bbox'],
            start_date,
            end_date,
            orbit_direction,
            satellites_filter
        )
        
        if not copernicus_products and not analysis['products_by_date']:
            logger.info("Error: No se encontraron productos ni localmente ni en Copernicus")
            return 1
        
        # Combinar productos locales con disponibles en Copernicus
        repository = None
        if args.check_processed:
            from insar_repository import InSARRepository
            repository = InSARRepository(repo_base_dir=args.repo_dir)
        
        analysis = merge_local_and_copernicus_products(
            analysis,
            copernicus_products,
            slc_dir,
            repository
        )
    
    # FILTRAR PRODUCTOS POR ÓRBITA, FECHA Y SATÉLITE
    filter_msg = f"Filtrando productos por órbita {orbit_direction}"
    if start_date and end_date:
        filter_msg += f" y período"
    if satellites_filter:
        filter_msg += f" y satélites {', '.join(satellites_filter)}"
    logger.info(f"\n{filter_msg}...")
    
    sys.path.insert(0, os.path.dirname(__file__))
    from processing_utils import extract_orbit_from_manifest
    
    filtered_products_by_date = defaultdict(list)
    filtered_count = 0
    total_count = 0
    filtered_by_date = 0
    filtered_by_satellite = 0
    
    for date, products in analysis['products_by_date'].items():
        # Convertir fecha a datetime para comparación (puede venir como str o datetime.date)
        if isinstance(date, str):
            product_date = datetime.strptime(date, '%Y-%m-%d')
        elif isinstance(date, datetime):
            product_date = date
        else:
            # Si es datetime.date, convertir a datetime
            product_date = datetime.combine(date, datetime.min.time())
        
        # Filtrar por rango de fechas si se especificó
        if start_date and product_date < start_date:
            filtered_by_date += len(products)
            continue
        if end_date and product_date > end_date:
            filtered_by_date += len(products)
            continue
        
        for product_info in products:
            total_count += 1
            product_path = Path(product_info['path'])
            product_name = product_path.name
            
            # Extraer satélite del nombre (S1A, S1C)
            satellite = product_name[:3]  # Primeros 3 caracteres (S1A, S1C)
            
            # Filtrar por satélite si se especificó
            if satellites_filter and satellite not in satellites_filter:
                filtered_by_satellite += 1
                continue
            
            # Si el producto está marcado como 'available' (de Copernicus), no verificar órbita
            if product_info.get('status') == 'available':
                # Ya filtrado por órbita en fetch_copernicus_products
                filtered_products_by_date[date].append(product_info)
                filtered_count += 1
                continue
            
            # Extraer órbita del manifest (solo para productos locales)
            product_orbit = extract_orbit_from_manifest(product_path)
            
            if product_orbit == orbit_direction:
                filtered_products_by_date[date].append(product_info)
                filtered_count += 1
    
    logger.info(f"  Total productos en directorio: {total_count + filtered_by_date + filtered_by_satellite}")
    if filtered_by_date > 0:
        logger.info(f"  Productos fuera del período (descartados): {filtered_by_date}")
    if filtered_by_satellite > 0:
        logger.info(f"  Productos de otros satélites (descartados): {filtered_by_satellite}")
    logger.info(f"  Productos {orbit_direction} en período: {filtered_count}")
    logger.info(f"  Productos otras órbitas (descartados): {total_count - filtered_count}")
    
    if not filtered_products_by_date:
        msg = f"\nError: No se encontraron productos SLC para órbita {orbit_direction}"
        if start_date and end_date:
            msg += f" en el período {args.start_date} a {args.end_date}"
        if satellites_filter:
            msg += f" de satélites {', '.join(satellites_filter)}"
        logger.info(msg)
        return 1
    
    # Reemplazar products_by_date con la versión filtrada
    analysis['products_by_date'] = filtered_products_by_date
    
    # Ahora mostrar análisis solo de productos filtrados
    logger.info(f"\nAnalizando cobertura de productos {orbit_direction}...")
    logger.info("-" * 80)
    
    # Mostrar información de productos filtrados
    for date in sorted(filtered_products_by_date.keys()):
        for product_info in filtered_products_by_date[date]:
            logger.info(f"\n{date} - {Path(product_info['path']).name}")
            logger.info(f"  Sub-swaths disponibles: {product_info.get('subswaths_available', [])}")
            
            # Mostrar qué sub-swaths cubren el AOI
            for subswath, covers in product_info.get('subswath_coverage', {}).items():
                if covers:
                    logger.info(f"    ✓ {subswath}: Cubre AOI")
                else:
                    logger.info(f"    ✗ {subswath}: NO cubre AOI")

    # Agrupar productos en series por sub-swath
    series = group_products_by_series(analysis)

    if not series:
        logger.info("Error: No se pudieron crear series")
        return 1

    # Mostrar resumen
    print_series_summary(series)

    # Guardar series
    saved_files = save_series(series, analysis['aoi_bbox'], orbit_direction, output_dir)

    # Crear manifest de procesamiento
    manifest_file = create_processing_manifest(series, analysis['aoi_bbox'],
                                               orbit_direction, output_dir)

    # Resumen final
    logger.info("\n" + "=" * 80)
    logger.info("RESUMEN FINAL")
    logger.info("=" * 80)
    logger.info(f"Dirección de órbita: {orbit_direction}")
    logger.info(f"Series generadas: {len(series)}")
    logger.info(f"Archivos creados: {len(saved_files) + 1}")  # +1 por el manifest

    logger.info("\nArchivos generados:")
    for f in saved_files:
        logger.info(f"  - {f.name}")
    logger.info(f"  - {manifest_file.name}")

    logger.info("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
