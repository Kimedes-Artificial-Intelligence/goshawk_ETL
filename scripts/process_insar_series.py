#!/usr/bin/env python3
"""
Script: process_insar_series.py
Descripción: Procesa una serie completa (InSAR + SAR + Estadísticas por par) desde configuración JSON

Uso:
  python3 process_insar_series.py selected_products_desc_iw1.json
  python3 process_insar_series.py selected_products_desc_iw2.json --output insar_desc_iw2
  python3 process_insar_series.py selected_products_desc_iw1.json --full-pipeline  # InSAR+estadísticas

IMPORTANTE: Solo usar IW1 o IW2 para procesamiento InSAR
  - IW1: Preferencia (mejor resolución y menor ruido)
  - IW2: Alternativa si IW1 no cubre el AOI
  - IW3: NO recomendado (alta distorsión geométrica y ruido)

Este script:
1. Lee la configuración JSON de una serie (sub-swath + órbita específicos)
2. Crea directorios de trabajo aislados para la serie
3. Crea enlaces simbólicos a los productos SLC seleccionados para fechas de la serie
4. Reutiliza preprocesamiento de caché global (data/preprocessed_slc_insar/)
5. Ejecuta el procesamiento InSAR completo (pares consecutivos)
6. [Opcional --full-pipeline] Calcula estadísticas por cada par temporal (coherencia InSAR)
7. Mantiene separados los resultados de cada serie (cada serie es independiente y autónoma)

NOTA 2025: Pipeline SLC-only, GRD deprecado
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import rasterio
from rasterio.mask import mask
from shapely import wkt
from shapely.geometry import mapping

# Importar utilidades de logging
sys.path.insert(0, str(Path(__file__).parent / 'scripts'))
from process_insar_gpt import create_pol_decomposition_xml
from logging_utils import LoggerConfig
from insar_repository import InSARRepository

# Logger se configurará después de crear el workspace
logger = None

# Info de productos finales (para preprocesamiento selectivo)
final_products_info = None


def load_series_config(config_file):
    """Carga configuración de la serie desde JSON"""
    if not os.path.exists(config_file):
        logger.error(f"ERROR: No existe el archivo de configuración: {config_file}")
        return None

    try:
        with open(config_file, 'r') as f:
            config = json.load(f)

        # Validar campos requeridos
        required_fields = ['orbit_direction', 'subswath', 'total_products', 'products']
        for field in required_fields:
            if field not in config:
                logger.error(f"ERROR: Falta campo requerido en config: {field}")
                return None

        return config

    except json.JSONDecodeError as e:
        logger.error(f"ERROR: JSON inválido: {e}")
        return None
    except Exception as e:
        logger.error(f"ERROR: {e}")
        return None


def validate_subswath_coverage_before_processing(series_config, workspace):
    """
    NUEVO: Valida la cobertura del subswath ANTES de iniciar el procesamiento.

    OPTIMIZACIÓN PARA DETECCIÓN DE HUMEDAD:
    Esta función verifica que el subswath seleccionado realmente cubre el AOI
    ANTES de lanzar el procesamiento masivo con GPT, ahorrando recursos.

    Args:
        series_config: Configuración de la serie
        workspace: Diccionario con rutas del workspace

    Returns:
        tuple: (is_valid: bool, coverage_pct: float, message: str)
    """
    from shapely import wkt as shapely_wkt
    from shapely.geometry import box
    import xml.etree.ElementTree as ET

    aoi_bbox = series_config.get('aoi_bbox', {})
    if not aoi_bbox:
        return (True, 100.0, "Sin AOI definido, procesando área completa")

    # Construir polígono AOI
    try:
        aoi_wkt = (
            f"POLYGON(("
            f"{aoi_bbox['min_lon']} {aoi_bbox['min_lat']}, "
            f"{aoi_bbox['max_lon']} {aoi_bbox['min_lat']}, "
            f"{aoi_bbox['max_lon']} {aoi_bbox['max_lat']}, "
            f"{aoi_bbox['min_lon']} {aoi_bbox['max_lat']}, "
            f"{aoi_bbox['min_lon']} {aoi_bbox['min_lat']}"
            f"))"
        )
        aoi_poly = shapely_wkt.loads(aoi_wkt)
    except Exception as e:
        return (False, 0.0, f"Error parseando AOI: {e}")

    subswath = series_config.get('subswath', 'IW1')

    # Verificar cobertura con el primer producto disponible
    slc_dir = workspace.get('slc')
    if not slc_dir or not slc_dir.exists():
        return (True, 0.0, "Sin productos SLC para validar, continuando")

    slc_products = list(slc_dir.glob('*.SAFE'))
    if not slc_products:
        return (True, 0.0, "Sin productos SLC para validar, continuando")

    # Analizar cobertura del primer producto
    product_path = slc_products[0]
    annotation_dir = product_path / 'annotation'

    if not annotation_dir.exists():
        return (True, 0.0, "Sin archivos de anotación, continuando")

    # Buscar archivo de anotación del subswath
    import glob as glob_module
    pattern = str(annotation_dir / f's1*-{subswath.lower()}-slc-*.xml')
    annotation_files = glob_module.glob(pattern)

    if not annotation_files:
        return (False, 0.0, f"Subswath {subswath} no encontrado en producto")

    try:
        tree = ET.parse(annotation_files[0])
        root = tree.getroot()

        lat_values = []
        lon_values = []

        for point in root.findall('.//geolocationGridPoint'):
            lat_elem = point.find('latitude')
            lon_elem = point.find('longitude')
            if lat_elem is not None and lon_elem is not None:
                lat_values.append(float(lat_elem.text))
                lon_values.append(float(lon_elem.text))

        if lat_values and lon_values:
            swath_box = box(
                min(lon_values), min(lat_values),
                max(lon_values), max(lat_values)
            )

            intersection = swath_box.intersection(aoi_poly)
            coverage = (intersection.area / aoi_poly.area) * 100 if aoi_poly.area > 0 else 0.0

            if coverage < 75.0:
                return (False, coverage,
                        f"Subswath {subswath} tiene solo {coverage:.1f}% de cobertura del AOI (mínimo requerido: 75%)")
            else:
                return (True, coverage,
                        f"✓ Subswath {subswath} cubre {coverage:.1f}% del AOI")

    except Exception as e:
        return (True, 0.0, f"Error analizando cobertura: {e}")

    return (True, 0.0, "Validación completada")


def create_series_workspace(series_config, output_dir):
    """
    Crea espacio de trabajo para la serie con enlaces simbólicos

    Estructura simplificada:
      workspace/
        ├── slc/                    # Symlinks a productos SLC
        ├── preprocessed_slc/       # Productos SLC preprocesados
        ├── insar/
        │   ├── short/              # Pares contiguos full swath
        │   ├── long/               # Pares largos full swath
        │   └── cropped/            # InSAR recortado al AOI
        ├── polarimetry/
        │   ├── *.dim               # Productos full swath
        │   └── cropped/            # Polarimetry recortado al AOI
        └── logs/

    Args:
        series_config: Configuración de la serie
        output_dir: Directorio base de salida

    Returns:
        dict: Rutas del workspace
    """
    orbit = series_config['orbit_direction'].lower()[:4]  # 'desc' o 'asce'
    subswath = series_config['subswath'].lower()  # 'iw1', 'iw2' o 'iw3'

    # Crear estructura de directorios simplificada
    workspace = {
        'base': Path(output_dir),
        'slc': Path(output_dir) / 'slc',
        'preprocessed': Path(output_dir) / 'preprocessed_slc',
        'insar': Path(output_dir) / 'insar',
        'insar_short': Path(output_dir) / 'insar' / 'short',
        'insar_long': Path(output_dir) / 'insar' / 'long',
        'insar_cropped': Path(output_dir) / 'insar' / 'cropped',
        'polarimetry': Path(output_dir) / 'polarimetry',
        'polarimetry_cropped': Path(output_dir) / 'polarimetry' / 'cropped',
        'logs': Path(output_dir) / 'logs',
    }

    # Crear directorios necesarios
    dirs_to_create = ['base', 'slc', 'preprocessed', 'insar', 'insar_short',
                      'insar_long', 'insar_cropped', 'polarimetry',
                      'polarimetry_cropped', 'logs']
    for key in dirs_to_create:
        workspace[key].mkdir(parents=True, exist_ok=True)

    # Ahora logger ya existe (inicializado antes de llamar esta función)
    logger.info(f"Creando workspace para serie:")
    logger.info(f"  Órbita: {series_config['orbit_direction']}")
    logger.info(f"  Sub-swath: {series_config['subswath']}")
    logger.info(f"  Directorio: {output_dir}")
    logger.info(f"  Productos: {series_config['total_products']}")

    return workspace


def create_symlinks(series_config, workspace):
    """
    Crea enlaces simbólicos a los productos SLC seleccionados

    Args:
        series_config: Configuración de la serie
        workspace: Diccionario con rutas del workspace

    Returns:
        int: Número de enlaces válidos (creados + existentes)
    """
    logger.info(f"\nVerificando enlaces simbólicos a productos SLC...")

    slc_dir = workspace['slc']
    valid_count = 0  # Enlaces válidos (nuevos + existentes)
    new_count = 0    # Solo enlaces nuevos

    for product_info in series_config['products']:
        src = Path(product_info['path'])

        if not src.exists():
            logger.warning(f"  ⚠️  Producto no encontrado: {src.name}")
            continue

        # Crear enlace simbólico en el directorio de la serie
        dst = slc_dir / src.name

        if dst.exists():
            if dst.is_symlink():
                logger.info(f"  ✓ Ya existe: {src.name}")
                valid_count += 1  # Contar enlaces existentes
            else:
                logger.warning(f"  ⚠️  Archivo existente (no symlink): {src.name}")
        else:
            try:
                dst.symlink_to(src.absolute())
                logger.info(f"  ✓ Enlazado: {src.name}")
                new_count += 1
                valid_count += 1
            except Exception as e:
                logger.error(f"  ✗ Error enlazando {src.name}: {e}")

    if new_count > 0:
        logger.info(f"\n✓ Enlaces SLC creados: {new_count}")
    logger.info(f"✓ Total enlaces SLC válidos: {valid_count}/{series_config['total_products']}")

    return valid_count


def create_grd_symlinks(series_config, workspace):
    """
    DEPRECATED: GRD ya no se usa en el pipeline (solo coherencia InSAR)
    Esta función ya no se ejecuta. Se mantiene para compatibilidad.

    Args:
        series_config: Configuración de la serie
        workspace: Diccionario con rutas del workspace

    Returns:
        int: 0 (función deprecada)
    """
    logger.warning("create_grd_symlinks está deprecada - GRD ya no se usa")
    return 0


def create_config_file(series_config, workspace):
    """
    Crea archivo config.txt para el procesamiento

    Args:
        series_config: Configuración de la serie
        workspace: Diccionario con rutas del workspace

    Returns:
        Path: Ruta al config.txt creado
    """
    config_file = workspace['base'] / 'config.txt'

    # Obtener AOI del config
    aoi_bbox = series_config.get('aoi_bbox', {})

    if aoi_bbox:
        # Convertir bbox a WKT
        wkt = (
            f"POLYGON(("
            f"{aoi_bbox['min_lon']} {aoi_bbox['min_lat']}, "
            f"{aoi_bbox['max_lon']} {aoi_bbox['min_lat']}, "
            f"{aoi_bbox['max_lon']} {aoi_bbox['max_lat']}, "
            f"{aoi_bbox['min_lon']} {aoi_bbox['max_lat']}, "
            f"{aoi_bbox['min_lon']} {aoi_bbox['min_lat']}"
            f"))"
        )
    else:
        wkt = ""

    orbit = series_config['orbit_direction'].lower()[:4]
    subswath = series_config['subswath']

    config_content = f"""# Configuración para serie {orbit.upper()}-{subswath}
# Generado automáticamente: {datetime.now().isoformat()}

# Directorios de datos SLC (relativos al workspace)
SLC_DIR="slc"
PREPROCESSED_SLC_DIR="preprocessed_slc"

# DEPRECATED: GRD ya no se usa en el pipeline
# GRD_DIR="grd"
# PREPROCESSED_GRD_DIR="preprocessed_grd"

# Directorio de salida InSAR (relativo al workspace)
# Los productos se guardan en insar/short/ o insar/long/
OUTPUT_DIR="insar"

# AOI
AOI="{wkt}"

# Configuración de serie
ORBIT_DIRECTION="{series_config['orbit_direction']}"
SUBSWATH="{subswath}"

# Parámetros de procesamiento
DETECTION_METHOD="weighted"
THRESHOLD_HIGH="0.7"
THRESHOLD_MEDIUM="0.5"
"""

    with open(config_file, 'w') as f:
        f.write(config_content)

    logger.info(f"\n✓ Config creado: {config_file}")

    return config_file


def check_and_setup_orbits(workspace):
    """
    Verifica disponibilidad de órbitas para los productos de la serie
    
    Las órbitas deben haber sido descargadas previamente con download_orbits.py
    Si faltan, SNAP las descargará automáticamente durante el procesamiento.
    
    Returns:
        bool: True (siempre, las órbitas se manejan automáticamente)
    """
    logger.info(f"\n{'=' * 80}")
    logger.info(f"PASO 1: VERIFICACIÓN DE ÓRBITAS")
    logger.info(f"{'=' * 80}\n")
    
    # Verificar si existen órbitas descargadas
    aux_dir = Path.home() / ".snap" / "auxdata" / "Orbits" / "Sentinel-1"
    if aux_dir.exists():
        orbit_files = list(aux_dir.rglob("*.EOF"))
        if orbit_files:
            logger.info(f"✓ Órbitas disponibles: {len(orbit_files)} archivos")
            logger.info(f"  Directorio: {aux_dir}")
        else:
            logger.info("ℹ️  No hay órbitas descargadas previamente")
            logger.info("  SNAP las descargará automáticamente si es necesario")
    else:
        logger.info("ℹ️  Directorio de órbitas no existe aún")
        logger.info("  SNAP las descargará automáticamente durante el procesamiento")
    
    return True


def check_missing_products(workspace, series_config, repository=None):
    """
    Verifica qué productos finales (InSAR, polarimetría) ya existen en el repositorio
    y determina qué falta procesar.

    Esta función implementa la optimización: si TODO ya está procesado, no necesitamos
    preprocesar ni procesar nada, solo hacer crop al AOI.

    Args:
        workspace: Diccionario con rutas del workspace
        series_config: Configuración de la serie (orbit, subswath, products, etc.)
        repository: InSARRepository instance (opcional)

    Returns:
        dict: {
            'all_exist': bool,  # Si todos los productos finales existen
            'missing_pairs': List[tuple],  # Pares (master, slave, type) que faltan
            'existing_pairs': List[tuple],  # Pares que ya existen
            'required_slc_dates': Set[str],  # Fechas SLC necesarias (YYYYMMDD)
            'existing_count': int,
            'missing_count': int,
            'track_number': int  # Track number del repositorio
        }
    """
    logger.info(f"\n{'=' * 80}")
    logger.info(f"VERIFICANDO PRODUCTOS EXISTENTES EN REPOSITORIO")
    logger.info(f"{'=' * 80}\n")

    # Inicializar resultado
    result = {
        'all_exist': False,
        'missing_pairs': [],
        'existing_pairs': [],
        'required_slc_dates': set(),
        'existing_count': 0,
        'missing_count': 0,
        'track_number': None
    }

    if not repository:
        logger.info("  ℹ️  Repositorio no habilitado - procesamiento completo necesario")
        # Generar todos los pares como faltantes
        products = series_config.get('products', [])
        dates = sorted([p['date'].replace('-', '') for p in products])

        # Pares short (consecutivos)
        for i in range(len(dates) - 1):
            result['missing_pairs'].append((dates[i], dates[i+1], 'short'))
            result['required_slc_dates'].add(dates[i])
            result['required_slc_dates'].add(dates[i+1])

        # Pares long (salto +2)
        for i in range(len(dates) - 2):
            result['missing_pairs'].append((dates[i], dates[i+2], 'long'))
            result['required_slc_dates'].add(dates[i])
            result['required_slc_dates'].add(dates[i+2])

        result['missing_count'] = len(result['missing_pairs'])
        return result

    # Obtener configuración de la serie
    orbit_direction = series_config.get('orbit_direction', 'DESCENDING')
    subswath = series_config.get('subswath', 'IW2')
    products = series_config.get('products', [])

    if not products:
        logger.warning("  ⚠️  No hay productos en la serie")
        return result

    # Obtener track number del primer producto
    first_product = products[0]
    track_number = first_product.get('relative_orbit')

    if not track_number:
        logger.warning("  ⚠️  No se pudo determinar track number - procesamiento completo necesario")
        return result

    result['track_number'] = track_number

    logger.info(f"  Serie: {orbit_direction} {subswath} Track {track_number}")
    logger.info(f"  Productos en serie: {len(products)}")

    # Obtener directorio del track en el repositorio
    try:
        track_dir = repository.get_track_dir(orbit_direction, subswath, track_number)
        logger.info(f"  Directorio repositorio: {track_dir}")
    except Exception as e:
        logger.warning(f"  ⚠️  Error accediendo repositorio: {e}")
        return result

    # Verificar si el directorio existe
    if not track_dir.exists():
        logger.info(f"  ℹ️  Track no existe en repositorio - procesamiento completo necesario")
        # Generar todos los pares como faltantes
        dates = sorted([p['date'].replace('-', '') for p in products])
        for i in range(len(dates) - 1):
            result['missing_pairs'].append((dates[i], dates[i+1], 'short'))
        for i in range(len(dates) - 2):
            result['missing_pairs'].append((dates[i], dates[i+2], 'long'))
        result['missing_count'] = len(result['missing_pairs'])
        return result

    # Generar lista de todos los pares esperados
    dates = sorted([p['date'].replace('-', '') for p in products])
    expected_pairs = []

    # Pares short (consecutivos)
    for i in range(len(dates) - 1):
        expected_pairs.append((dates[i], dates[i+1], 'short'))

    # Pares long (salto +2)
    for i in range(len(dates) - 2):
        expected_pairs.append((dates[i], dates[i+2], 'long'))

    logger.info(f"  Pares esperados: {len(expected_pairs)}")
    logger.info(f"    - Short (consecutivos): {len(dates) - 1}")
    logger.info(f"    - Long (salto +2): {len(dates) - 2}")

    # Verificar qué pares existen en el repositorio
    insar_short_dir = track_dir / "insar" / "short"
    insar_long_dir = track_dir / "insar" / "long"

    existing_products = set()

    # Buscar en pares short
    if insar_short_dir.exists():
        for dim_file in insar_short_dir.glob("Ifg_*.dim"):
            existing_products.add(dim_file.stem)

    # Buscar en pares long
    if insar_long_dir.exists():
        for dim_file in insar_long_dir.glob("Ifg_*_LONG.dim"):
            existing_products.add(dim_file.stem)

    logger.info(f"  Productos existentes en repositorio: {len(existing_products)}")

    # Clasificar pares
    for master_date, slave_date, pair_type in expected_pairs:
        # Construir nombre esperado del producto
        if pair_type == 'short':
            product_name = f"Ifg_{master_date}_{slave_date}"
        else:  # long
            product_name = f"Ifg_{master_date}_{slave_date}_LONG"

        if product_name in existing_products:
            result['existing_pairs'].append((master_date, slave_date, pair_type))
            result['existing_count'] += 1
        else:
            result['missing_pairs'].append((master_date, slave_date, pair_type))
            result['missing_count'] += 1
            # Añadir SLC necesarios para este par
            result['required_slc_dates'].add(master_date)
            result['required_slc_dates'].add(slave_date)

    # Determinar si todo existe
    result['all_exist'] = (result['missing_count'] == 0)

    logger.info(f"\n  RESUMEN:")
    logger.info(f"    ✓ Pares existentes: {result['existing_count']}")
    logger.info(f"    ✗ Pares faltantes: {result['missing_count']}")

    if result['all_exist']:
        logger.info(f"    🎉 TODOS los productos finales ya existen!")
        logger.info(f"    → Se puede saltar preprocesamiento y procesamiento")
    elif result['missing_count'] < len(expected_pairs):
        logger.info(f"    → Procesamiento parcial: solo {result['missing_count']} pares")
        logger.info(f"    → SLC necesarios: {len(result['required_slc_dates'])}")
    else:
        logger.info(f"    → Procesamiento completo necesario")

    logger.info("")

    return result


def check_global_preprocessed_cache(series_config, required_slc_dates=None):
    """
    Busca SLC preprocesados en la caché global data/preprocessed_slc/

    La caché global tiene la estructura:
        data/preprocessed_slc/{orbit}_{subswath}/t{track}/{fecha}/producto_split.dim

    Args:
        series_config: Configuración de la serie (para orbit, subswath, track)
        required_slc_dates: Set de fechas SLC necesarias (YYYYMMDD). Si None, no filtra.

    Returns:
        dict: {
            'found_products': {fecha: Path_to_dim_file},  # SLC encontrados en caché
            'missing_dates': Set[str],  # Fechas que faltan en caché
            'cache_dir': Path  # Directorio de caché para este track
        }
    """
    from pathlib import Path

    result = {
        'found_products': {},
        'missing_dates': set(),
        'cache_dir': None
    }

    # Obtener configuración
    orbit_direction = series_config.get('orbit_direction', 'DESCENDING')
    subswath = series_config.get('subswath', 'IW2')
    products = series_config.get('products', [])

    if not products:
        return result

    # Obtener track number
    first_product = products[0]
    track_number = first_product.get('relative_orbit')

    if not track_number:
        logger.debug("  No se pudo determinar track number para caché global")
        return result

    # Construir path a caché global
    project_root = Path.cwd()
    orbit_suffix = "desc" if orbit_direction == "DESCENDING" else "asce"
    subswath_lower = subswath.lower()
    cache_dir = project_root / "data" / "preprocessed_slc" / f"{orbit_suffix}_{subswath_lower}" / f"t{track_number:03d}"

    result['cache_dir'] = cache_dir

    if not cache_dir.exists():
        logger.debug(f"  Caché global no existe: {cache_dir}")
        if required_slc_dates:
            result['missing_dates'] = required_slc_dates
        return result

    # Buscar productos en caché
    logger.info(f"\n🔍 Buscando SLC preprocesados en caché global...")
    logger.info(f"  Caché: {cache_dir}")

    # Determinar fechas a buscar
    if required_slc_dates:
        dates_to_check = required_slc_dates
    else:
        dates_to_check = set(p['date'].replace('-', '') for p in products)

    for date in sorted(dates_to_check):
        date_dir = cache_dir / date

        if date_dir.exists():
            # Buscar archivos .dim en el directorio de fecha
            dim_files = list(date_dir.glob("*.dim"))

            if dim_files:
                # Tomar el primer .dim encontrado
                result['found_products'][date] = dim_files[0]
                logger.info(f"  ✓ {date}: {dim_files[0].name}")
            else:
                result['missing_dates'].add(date)
                logger.debug(f"  ✗ {date}: directorio existe pero sin .dim")
        else:
            result['missing_dates'].add(date)
            logger.debug(f"  ✗ {date}: no encontrado")

    if result['found_products']:
        logger.info(f"\n  Resumen caché global:")
        logger.info(f"    ✓ Encontrados: {len(result['found_products'])}")
        logger.info(f"    ✗ Faltantes: {len(result['missing_dates'])}")
    else:
        logger.info(f"  ℹ️  No se encontraron SLC en caché global")

    return result


def run_preprocessing(workspace, config_file, required_slc_dates=None):
    """
    Ejecuta el pre-procesamiento de productos SLC para InSAR

    Preprocesamiento local por proyecto con AOI específico:
    - Aplica TOPSAR-Split por subswath
    - Recorta al AOI del proyecto (Subset)
    - NO aplica Deburst (se mantiene estructura de bursts para InSAR)
    - Productos más pequeños, procesamiento InSAR más rápido

    Args:
        workspace: Diccionario con rutas del workspace
        config_file: Ruta al archivo de configuración
        required_slc_dates: Set[str] opcional con fechas (YYYYMMDD) de SLC a preprocesar.
                           Si None, preproces todos los SLC disponibles.

    Returns:
        bool: True si el pre-procesamiento fue exitoso
    """
    logger.info(f"\n{'=' * 80}")
    if required_slc_dates:
        logger.info(f"PASO 2: PRE-PROCESAMIENTO SELECTIVO ({len(required_slc_dates)} SLC necesarios)")
    else:
        logger.info(f"PASO 2: PRE-PROCESAMIENTO (TOPSAR-Split + Subset AOI)")
    logger.info(f"{'=' * 80}\n")

    # OPTIMIZACIÓN: Buscar primero en caché global data/preprocessed_slc/
    # Leer series_config desde config_file
    series_config = {}
    if config_file.exists():
        with open(config_file, 'r') as f:
            for line in f:
                if '=' in line and not line.strip().startswith('#'):
                    key, value = line.strip().split('=', 1)
                    series_config[key] = value.strip('"')

    # Obtener info para buscar en caché global
    if not series_config:
        # Fallback: intentar cargar desde workspace
        config_json = workspace['base'].parent / 'selected_products.json'
        if config_json.exists():
            import json
            with open(config_json) as f:
                series_config = json.load(f)

    cache_result = check_global_preprocessed_cache(series_config, required_slc_dates)

    # Crear symlinks para productos encontrados en caché
    if cache_result['found_products']:
        logger.info(f"\n📦 Creando symlinks desde caché global...")
        workspace['preprocessed'].mkdir(parents=True, exist_ok=True)

        for date, dim_path in cache_result['found_products'].items():
            # Crear symlink al .dim
            target_name = dim_path.name
            link_path = workspace['preprocessed'] / target_name

            if not link_path.exists():
                link_path.symlink_to(dim_path.absolute())
                logger.info(f"  ✓ {date}: {target_name}")

                # Symlink al .data
                dim_data = dim_path.with_suffix('.data')
                link_data = link_path.with_suffix('.data')
                if dim_data.exists() and not link_data.exists():
                    link_data.symlink_to(dim_data.absolute())

        logger.info(f"\n  ✓ {len(cache_result['found_products'])} SLC obtenidos desde caché global")

        # Actualizar required_slc_dates para solo procesar los faltantes
        if required_slc_dates and cache_result['missing_dates']:
            required_slc_dates = cache_result['missing_dates']
            logger.info(f"  → Solo preprocesar {len(required_slc_dates)} SLC faltantes\n")
        elif not cache_result['missing_dates']:
            logger.info(f"  🎉 TODOS los SLC necesarios están en caché!")
            logger.info(f"  → Saltando preprocesamiento completamente\n")
            return True

    # Verificar si ya existen productos pre-procesados (además de los de caché)
    existing_preprocessed = list(workspace['preprocessed'].glob('*.dim'))
    if len(existing_preprocessed) >= 1:
        logger.info(f"ℹ️  Ya existen {len(existing_preprocessed)} productos preprocesados")
        logger.info(f"   → Procesamiento incremental: solo se preprocesarán nuevos SLC\n")

    # Si se especificaron fechas requeridas, filtrar SLC necesarios
    temp_slc_dir = None
    original_slc_path = workspace['slc']

    if required_slc_dates:
        logger.info(f"  Filtrando SLC por fechas requeridas: {sorted(required_slc_dates)}\n")

        # Crear directorio temporal con symlinks solo a SLC necesarios
        import tempfile
        from processing_utils import extract_date_from_filename

        temp_slc_dir = Path(tempfile.mkdtemp(prefix="slc_filtered_"))

        # Buscar SLC que coincidan con las fechas requeridas
        filtered_count = 0
        for slc_path in workspace['slc'].glob('*.SAFE'):
            date_str = extract_date_from_filename(slc_path.name)
            if date_str and date_str[:8] in required_slc_dates:
                # Crear symlink al SLC necesario
                link_path = temp_slc_dir / slc_path.name
                link_path.symlink_to(slc_path.absolute())
                filtered_count += 1
                logger.info(f"    ✓ Incluido: {slc_path.name[:60]}")

        logger.info(f"\n  SLC filtrados: {filtered_count}/{len(list(workspace['slc'].glob('*.SAFE')))}")

        # Temporalmente reemplazar el directorio SLC
        workspace['slc'] = temp_slc_dir
        
        # IMPORTANTE: Actualizar config.txt para usar el directorio temporal
        temp_config = workspace['base'] / 'config_incremental.txt'
        
        # Leer config original y modificar SLC_DIR
        with open(config_file, 'r') as f_in:
            with open(temp_config, 'w') as f_out:
                for line in f_in:
                    if line.startswith('SLC_DIR='):
                        # Usar path absoluto al directorio temporal
                        f_out.write(f'SLC_DIR="{temp_slc_dir.absolute()}"\n')
                    else:
                        f_out.write(line)
        
        # Usar config temporal
        config_file = temp_config
        logger.info(f"  ✓ Config temporal creado con SLC filtrados\n")

    # Comando de pre-procesamiento con --insar-mode
    # Rutas absolutas para ejecutar desde el workspace
    project_root = Path.cwd()
    cmd = [
        sys.executable,
        str(project_root / "scripts" / "preprocess_products.py"),
        "--slc",
        "--insar-mode",  # Mantiene estructura de bursts, aplica Split + Subset
        "--config", str(config_file.name)  # Nombre relativo (config.txt está en el workspace)
    ]

    logger.info(f"\nEjecutando: {' '.join(cmd)}\n")

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(workspace['base'])  # Ejecutar desde el workspace para que las rutas relativas funcionen
    )

    # Limpiar directorio temporal y restaurar workspace
    if temp_slc_dir:
        import shutil
        shutil.rmtree(temp_slc_dir, ignore_errors=True)
        workspace['slc'] = original_slc_path

    # Mostrar salida
    if result.stdout:
        lines = result.stdout.strip().split('\n')
        for line in lines[-20:]:
            logger.info(line)

    if result.returncode == 0:
        # Verificar que se crearon los productos
        preprocessed_count = len(list(workspace['preprocessed'].glob('*.dim')))
        logger.info(f"\n✓ Pre-procesamiento completado")
        logger.info(f"  Productos preprocesados: {preprocessed_count}")
        return True
    else:
        logger.warning(f"\n⚠️  Pre-procesamiento tuvo problemas")
        logger.warning("  Se usarán productos originales sin pre-procesar")
        return False


def run_insar_processing(workspace, config_file, series_config, use_preprocessed=False,
                         use_repository=False, save_to_repository=False, missing_info=None):
    """
    Ejecuta el procesamiento InSAR para la serie

    Args:
        workspace: Diccionario con rutas del workspace
        config_file: Ruta al archivo de configuración
        series_config: Configuración de la serie
        use_preprocessed: Si usar productos pre-procesados
        use_repository: Buscar productos en repositorio antes de procesar
        save_to_repository: Guardar productos al repositorio después de procesar
        missing_info: Dict con información sobre productos faltantes (de check_missing_products)

    Returns:
        bool: True si el procesamiento fue exitoso
    """
    logger.info(f"\n{'=' * 80}")
    logger.info(f"PASO 3: PROCESAMIENTO INSAR")
    logger.info(f"{'=' * 80}\n")

    orbit = series_config['orbit_direction']
    subswath = series_config['subswath']

    logger.info(f"  Serie: {orbit}-{subswath}")
    logger.info(f"  Productos: {series_config['total_products']}")
    logger.info(f"  Workspace: {workspace['base']}")
    logger.info("")

    # OPTIMIZACIÓN: Si todos los productos ya existen, saltar procesamiento
    if missing_info and missing_info.get('all_exist', False):
        logger.info(f"🎉 TODOS los productos InSAR ya existen en el repositorio!")
        logger.info(f"  ✓ {missing_info['existing_count']} pares ya procesados")
        logger.info(f"  → Saltando procesamiento InSAR completamente\n")
        return True

    # Si hay procesamiento parcial, informar
    if missing_info and missing_info.get('missing_count', 0) > 0:
        logger.info(f"  ℹ️  Procesamiento parcial necesario:")
        logger.info(f"     ✓ {missing_info['existing_count']} pares ya en repositorio")
        logger.info(f"     ✗ {missing_info['missing_count']} pares por procesar")
        logger.info(f"  → Solo se procesarán los pares faltantes\n")

    # VERIFICACIÓN CRÍTICA: Si se solicita usar preprocesados, verificar que existen
    if use_preprocessed:
        preprocessed_files = list(workspace['preprocessed'].glob('*.dim'))
        if len(preprocessed_files) < 2:
            logger.warning(f"\n{'=' * 80}")
            logger.warning(f"⚠️  ADVERTENCIA: Preprocesado solicitado pero no hay productos")
            logger.warning(f"{'=' * 80}")
            logger.warning(f"  Directorio: {workspace['preprocessed']}")
            logger.warning(f"  Productos encontrados: {len(preprocessed_files)}")
            logger.warning(f"  Productos requeridos: >= 2")
            logger.warning("")
            logger.warning(f"  → Ejecutando preprocesamiento automáticamente...\n")

            # Ejecutar preprocesamiento (solo SLC necesarios si tenemos missing_info)
            required_slc_dates = missing_info.get('required_slc_dates') if missing_info else None
            preprocessing_success = run_preprocessing(workspace, config_file, required_slc_dates=required_slc_dates)

            if not preprocessing_success:
                logger.error(f"✗ Preprocesamiento falló - abortando InSAR")
                return False

            # Verificar de nuevo
            preprocessed_files = list(workspace['preprocessed'].glob('*.dim'))
            if len(preprocessed_files) < 2:
                logger.error(f"✗ Aún no hay suficientes productos preprocesados")
                logger.error(f"  Se encontraron {len(preprocessed_files)} productos, se necesitan al menos 2")
                return False

            logger.info(f"✓ Preprocesamiento completado: {len(preprocessed_files)} productos\n")

    # Log file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = workspace['logs'] / f"insar_{timestamp}.log"

    # Backup del config.txt global
    global_config = Path("../config.txt")
    global_config_backup = None

    if global_config.exists():
        global_config_backup = Path("config.txt.backup_insar")
        import shutil
        shutil.copy(global_config, global_config_backup)

    try:
        # Copiar config de la serie al config.txt global
        import shutil
        shutil.copy(config_file, global_config)

        # Comando de procesamiento InSAR (usar rutas absolutas)
        project_root = Path.cwd()
        cmd = [
            sys.executable,
            str(project_root / "scripts" / "process_insar_gpt.py")
        ]

        if use_preprocessed:
            cmd.append("--use-preprocessed")
            logger.info(f"  → Usando productos pre-procesados ({len(preprocessed_files)} archivos)")

        if use_repository:
            cmd.append("--use-repository")
            logger.info(f"  → Verificando repositorio compartido antes de procesar")

        if save_to_repository:
            cmd.append("--save-to-repository")
            logger.info(f"  → Guardando productos al repositorio compartido")

        logger.info(f"Ejecutando: {' '.join(cmd)}\n")

        with open(log_file, 'w') as f:
            f.write(f"Procesamiento InSAR - {datetime.now()}\n")
            f.write(f"Serie: {orbit}-{subswath}\n")
            f.write(f"Comando: {' '.join(cmd)}\n")
            f.write("=" * 80 + "\n\n")

        # Ejecutar procesamiento desde el workspace
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(workspace['base'])
        )

        # Guardar salida en log
        with open(log_file, 'a') as f:
            f.write(result.stdout)

        # Mostrar salida en tiempo real (últimas líneas)
        if result.stdout:
            lines = result.stdout.strip().split('\n')
            for line in lines[-30:]:
                logger.info(line)

        success = result.returncode == 0

        if success:
            logger.info(f"\n✓ Procesamiento InSAR completado")
            logger.info(f"  Log: {log_file}")
        else:
            logger.error(f"\n✗ Error en procesamiento InSAR")
            logger.error(f"  Exit code: {result.returncode}")
            logger.error(f"  Log: {log_file}")

        return success

    except Exception as e:
        logger.error(f"\n✗ Excepción durante procesamiento: {e}")
        return False

    finally:
        # Restaurar config.txt global
        if global_config_backup and global_config_backup.exists():
            shutil.copy(global_config_backup, global_config)
            global_config_backup.unlink()


def run_insar_crop(workspace, series_config):
    """
    Recorta productos InSAR al AOI para reducir tamaño y mejorar procesamiento
    
    Args:
        workspace: Diccionario con rutas del workspace
        series_config: Configuración de la serie
    
    Returns:
        bool: True si tuvo éxito, False en caso contrario
    """
    logger.info(f"\n{'=' * 80}")
    logger.info(f"PASO 4.5: RECORTE PRODUCTOS InSAR AL AOI")
    logger.info(f"{'=' * 80}\n")

    # No cambiar directorio - usar rutas absolutas
    original_dir = os.getcwd()
    workspace_abs = os.path.abspath(workspace['base'])

    try:
        # Ejecutar crop_insar_to_aoi.py con ruta absoluta
        cmd = [
            sys.executable,
            os.path.join(original_dir, "scripts/crop_insar_to_aoi.py"),
            workspace_abs
        ]

        logger.info(f"Comando: {' '.join(cmd)}")
        logger.info("")

        result = subprocess.run(
            cmd,
            capture_output=False,
            text=True,
            timeout=600  # 10 min timeout
        )

        if result.returncode == 0:
            logger.info(f"\n✓ Productos InSAR recortados al AOI")
            return True
        else:
            logger.warning(f"\n⚠️  Recorte InSAR completó con advertencias")
            logger.warning("  Se usarán productos originales sin recortar")
            return True  # No es crítico, continuar

    except subprocess.TimeoutExpired:
        logger.warning(f"\n⚠️  Timeout recortando productos InSAR")
        return True  # No es crítico
    except Exception as e:
        logger.warning(f"\n⚠️  Error ejecutando crop_insar_to_aoi.py: {e}")
        return True  # No es crítico


def run_polarimetric_crop(workspace, series_config):
    """
    Recorta productos polarimétricos al AOI para reducir tamaño

    Args:
        workspace: Diccionario con rutas del workspace
        series_config: Configuración de la serie

    Returns:
        bool: True si tuvo éxito, False en caso contrario
    """
    logger.info(f"\n{'=' * 80}")
    logger.info(f"PASO 5.5: RECORTE PRODUCTOS POLARIMÉTRICOS AL AOI")
    logger.info(f"{'=' * 80}\n")

    # Verificar si hay productos polarimétricos
    pol_dir = workspace.get('polarimetry')
    if not pol_dir or not pol_dir.exists():
        logger.info("  No hay productos polarimétricos para recortar")
        return True

    pol_products = list(pol_dir.glob('*_HAAlpha.dim'))
    if not pol_products:
        logger.info("  No hay productos polarimétricos para recortar")
        return True

    logger.info(f"  Productos polarimétricos encontrados: {len(pol_products)}")

    # No cambiar directorio - usar rutas absolutas
    original_dir = os.getcwd()
    workspace_abs = os.path.abspath(workspace['base'])

    try:
        # Ejecutar crop_polarimetry_to_aoi.py con ruta absoluta
        cmd = [
            sys.executable,
            os.path.join(original_dir, "scripts/crop_polarimetry_to_aoi.py"),
            workspace_abs
        ]

        logger.info(f"Comando: {' '.join(cmd)}")
        logger.info("")

        result = subprocess.run(
            cmd,
            capture_output=False,
            text=True,
            timeout=600  # 10 min timeout
        )

        if result.returncode == 0:
            logger.info(f"\n✓ Productos polarimétricos recortados al AOI")
            return True
        else:
            logger.warning(f"\n⚠️  Recorte polarimétrico completó con advertencias")
            logger.warning("  Se usarán productos originales sin recortar")
            return True  # No es crítico, continuar

    except subprocess.TimeoutExpired:
        logger.warning(f"\n⚠️  Timeout recortando productos polarimétricos")
        return True  # No es crítico
    except Exception as e:
        logger.warning(f"\n⚠️  Error ejecutando crop_polarimetry_to_aoi.py: {e}")
        return True  # No es crítico


def run_statistics(workspace, series_config):
    """
    DEPRECATED: Ya no se usa (pairs/ eliminado del pipeline)

    Las estadísticas por par temporal se calculaban extrayendo coherencia
    de cada interferograma y guardándola en pairs/pair_*/coherence.tif

    Ahora la coherencia está directamente disponible en:
    - insar/short/Ifg_*.dim (banda coherence en .data/)
    - insar/long/Ifg_*.dim (banda coherence en .data/)
    - insar/cropped/*.tif (recortado al AOI, si necesitas TIF)

    Esta función se mantiene solo para compatibilidad pero no hace nada.
    """
    logger.warning("run_statistics() está DEPRECATED - coherencia disponible en productos InSAR")
    return True  # Retornar éxito para compatibilidad


def validate_subswath_coverage(workspace):
    """
    Valida que el subswath tenga cobertura real del AOI analizando los pares de coherencia.
    IMPORTANTE: Recorta cada coherencia al AOI antes de calcular cobertura.
    Si todos los pares tienen 0% de datos válidos dentro del AOI, el subswath no cubre el AOI.

    Lee coherencia directamente de los productos InSAR en insar/short/ y insar/long/

    Returns:
        tuple: (is_valid: bool, valid_pairs: int, total_pairs: int, avg_coverage: float)
    """
    # Buscar productos InSAR en short/ y long/
    insar_short_dir = workspace.get('insar_short')
    insar_long_dir = workspace.get('insar_long')

    insar_products = []
    if insar_short_dir and insar_short_dir.exists():
        insar_products.extend(list(insar_short_dir.glob('Ifg_*.dim')))
    if insar_long_dir and insar_long_dir.exists():
        insar_products.extend(list(insar_long_dir.glob('Ifg_*.dim')))

    if not insar_products:
        if logger:
            logger.warning(f"⚠️  No se encontraron productos InSAR")
        return (False, 0, 0, 0.0)
    
    # Leer AOI del config.txt
    config_file = os.path.join(workspace['base'], 'config.txt')
    aoi_wkt_str = None
    
    if os.path.exists(config_file):
        with open(config_file, 'r') as f:
            for line in f:
                if line.startswith('AOI='):
                    aoi_wkt_str = line.split('=', 1)[1].strip().strip('"')
                    break
    
    if not aoi_wkt_str or aoi_wkt_str == "":
        if logger:
            logger.warning(f"⚠️  No se encontró AOI en config.txt, usando imagen completa")
        aoi_geom = None
    else:
        try:
            aoi_geom = wkt.loads(aoi_wkt_str)
            geoms = [mapping(aoi_geom)]
        except Exception as e:
            if logger:
                logger.warning(f"⚠️  Error parseando AOI WKT: {e}")
            aoi_geom = None

    total_pairs = len(insar_products)
    valid_pairs = 0
    coverage_sum = 0.0

    if logger:
        logger.info(f"\nValidando cobertura real del subswath en el AOI...")
        logger.info(f"  Analizando {total_pairs} productos InSAR")
        if aoi_geom:
            logger.info(f"  AOI detectado: recortando coherencias antes de validar")

    for insar_product in insar_products:
        # Buscar banda de coherencia en el .data
        data_dir = insar_product.with_suffix('.data')
        if not data_dir.exists():
            continue

        # Buscar archivo de coherencia (.img que contenga 'coh')
        coherence_files = list(data_dir.glob('*coh*.img'))
        if not coherence_files:
            continue

        coherence_file = coherence_files[0]

        try:
            with rasterio.open(coherence_file) as src:
                # Si hay AOI, recortar primero
                if aoi_geom:
                    try:
                        out_image, out_transform = mask(src, geoms, crop=True, all_touched=True, nodata=0)
                        data = out_image[0]  # Primera banda
                    except ValueError as e:
                        # AOI fuera del raster
                        if logger:
                            logger.warning(f"  ⚠️  AOI fuera del raster en {insar_product.name}: {e}")
                        continue
                else:
                    data = src.read(1)
                
                # Contar píxeles válidos DENTRO del AOI
                valid_mask = np.isfinite(data) & (data != 0)
                valid_pixels = np.sum(valid_mask)
                total_pixels = data.size
                coverage_pct = (valid_pixels / total_pixels * 100) if total_pixels > 0 else 0.0
                
                coverage_sum += coverage_pct
                
                if coverage_pct >= 10.0:  # Umbral mínimo de 10%
                    valid_pairs += 1


        except Exception as e:
            if logger:
                logger.warning(f"  ⚠️  Error leyendo {insar_product.name}: {e}")
            continue
    
    avg_coverage = (coverage_sum / total_pairs) if total_pairs > 0 else 0.0
    is_valid = valid_pairs > 0  # Al menos 1 par debe tener >= 10% de cobertura
    
    if logger:
        logger.info(f"  Pares válidos: {valid_pairs}/{total_pairs}")
        logger.info(f"  Cobertura promedio en AOI: {avg_coverage:.1f}%")
    
    if not is_valid:
        if logger:
            logger.error(f"\n{'=' * 80}")
            logger.error(f"✗ VALIDACIÓN FALLIDA: Subswath sin cobertura del AOI")
            logger.error(f"{'=' * 80}")
            logger.error(f"  Todos los pares tienen <10% de datos válidos DENTRO del AOI")
            logger.error(f"  Cobertura promedio en AOI: {avg_coverage:.2f}%")
            logger.error(f"  Pares analizados: {total_pairs}")
            logger.error(f"  Pares con cobertura >= 10%: {valid_pairs}")
            logger.error(f"  El subswath NO cubre el área de interés")
            logger.error(f"\nACCIÓN RECOMENDADA:")
            logger.error(f"  1. Este subswath será eliminado del procesamiento")
            logger.error(f"  2. Verificar si el AOI está en frontera entre subswaths")
            logger.error(f"  3. Considerar procesar con el otro subswath (IW1 ↔ IW2)")
            logger.error(f"  4. Usar preprocess_products.py para detectar subswaths disponibles")
            logger.error(f"{'=' * 80}\n")
    else:
        if logger:
            logger.info(f"✓ Validación exitosa: Subswath cubre el AOI")
            logger.info(f"  Cobertura promedio: {avg_coverage:.1f}%")
            logger.info(f"  Pares válidos: {valid_pairs}/{total_pairs}")
    
    return (is_valid, valid_pairs, total_pairs, avg_coverage)


def cleanup_intermediate_files(workspace, series_config):
    """
    Limpia archivos intermedios después del procesamiento exitoso.

    Elimina SOLO:
    - Productos SLC preprocesados (.dim/.data)

    Mantiene:
    - Enlaces simbólicos SLC (no ocupan espacio)
    - Productos InSAR finales (short/long)
    - Productos InSAR recortados
    - Productos polarimétricos
    - Logs
    """
    logger.info("Analizando archivos intermedios a limpiar...")

    base_dir = workspace['base']
    files_to_remove = []
    dirs_to_remove = []
    total_size = 0

    # Productos SLC preprocesados
    # IMPORTANTE: NO eliminar si es symlink a caché global compartida
    preprocessed_slc = workspace.get('preprocessed')
    if preprocessed_slc and preprocessed_slc.exists():
        if preprocessed_slc.is_symlink():
            # Symlink a caché global - PROTEGIDO
            target = preprocessed_slc.resolve()
            logger.info(f"  ✅ preprocessed_slc: symlink a caché global (PROTEGIDO)")
            logger.info(f"      → {target}")
            # Solo eliminar el symlink, no el contenido
            dirs_to_remove.append(('symlink', preprocessed_slc))
        else:
            # Directorio local - se puede eliminar completamente
            logger.info(f"  📦 preprocessed_slc: {preprocessed_slc}")
            for item in preprocessed_slc.rglob('*'):
                if item.is_file():
                    try:
                        size = item.stat().st_size
                        total_size += size
                        files_to_remove.append((item, size))
                    except:
                        pass
            if preprocessed_slc.exists():
                dirs_to_remove.append(('dir', preprocessed_slc))
    
    # Convertir tamaño a formato legible
    def human_size(bytes):
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes < 1024.0:
                return f"{bytes:.1f} {unit}"
            bytes /= 1024.0
        return f"{bytes:.1f} PB"
    
    if total_size == 0:
        logger.info(f"✓ No hay archivos intermedios para limpiar")
        return
    
    logger.info(f"\nArchivos a eliminar: {len(files_to_remove)}")
    logger.info(f"Directorios a eliminar: {len(dirs_to_remove)}")
    logger.info(f"Espacio a liberar: {human_size(total_size)}\n")
    
    # Ejecutar limpieza
    try:
        # Eliminar directorios completos
        for item in dirs_to_remove:
            if isinstance(item, tuple):
                dir_type, dir_path = item
            else:
                # Compatibilidad con código antiguo
                dir_type, dir_path = 'dir', item
            
            if dir_path.exists():
                try:
                    if dir_type == 'symlink':
                        # Solo eliminar el symlink, no el destino
                        dir_path.unlink()
                        logger.info(f"  ✓ Eliminado symlink: {dir_path.name}")
                    else:
                        # Eliminar directorio completo
                        shutil.rmtree(dir_path)
                        logger.info(f"  ✓ Eliminado: {dir_path.name}/")
                except Exception as e:
                    logger.warning(f"  ⚠️  No se pudo eliminar {dir_path.name}/: {e}")
        
        # Eliminar archivos sueltos
        for file_path, size in files_to_remove:
            if file_path.exists() and file_path.parent.exists():
                try:
                    file_path.unlink()
                except:
                    pass
        
        logger.info(f"\n✓ Limpieza completada")
        logger.info(f"  Espacio liberado: {human_size(total_size)}")
        
    except Exception as e:
        logger.warning(f"⚠️  Error durante limpieza: {e}")


def cleanup_invalid_subswath(workspace, series_config):
    """
    Elimina un subswath inválido (sin cobertura) del directorio de procesamiento.
    Incluye limpieza de archivos intermedios (preprocessed_slc) antes de eliminar.
    """
    base_dir = workspace['base']
    subswath = series_config.get('subswath', 'unknown')
    orbit = series_config.get('orbit_direction', 'unknown')
    
    logger.info(f"\nEliminando subswath inválido...")
    logger.info(f"  Directorio: {base_dir}")
    
    try:
        # PASO 1: Limpiar archivos intermedios primero (preprocessed_slc)
        # Importante: Si hay symlinks a caché global, solo eliminar el link, no el destino
        preprocessed_slc = workspace.get('preprocessed')
        if preprocessed_slc and preprocessed_slc.exists():
            if preprocessed_slc.is_symlink():
                # Symlink a caché global - solo eliminar el link
                preprocessed_slc.unlink()
                logger.info(f"  ✓ Symlink eliminado (caché global preservada)")
            elif preprocessed_slc.is_dir():
                # Directorio local - eliminar contenido
                size_bytes = sum(f.stat().st_size for f in preprocessed_slc.rglob('*') if f.is_file())
                shutil.rmtree(preprocessed_slc)
                size_mb = size_bytes / (1024 * 1024)
                logger.info(f"  ✓ preprocessed_slc eliminado ({size_mb:.1f} MB)")
        
        # PASO 2: Eliminar todo el directorio del subswath
        if os.path.exists(base_dir):
            shutil.rmtree(base_dir)
            logger.info(f"✓ Subswath completo eliminado")
            logger.info(f"  ✅ Caché global preservada (no afectada)")
            
            # Crear archivo de documentación sobre por qué se eliminó
            parent_dir = os.path.dirname(base_dir)
            info_file = os.path.join(parent_dir, f".{os.path.basename(base_dir)}_REMOVED.txt")
            
            with open(info_file, 'w') as f:
                f.write(f"Subswath eliminado: {subswath} ({orbit})\n")
                f.write(f"Fecha: {datetime.now().isoformat()}\n")
                f.write(f"Razón: Sin cobertura del AOI (0% de datos válidos)\n")
                f.write(f"\n")
                f.write(f"El análisis de productos InSAR mostró que este subswath\n")
                f.write(f"no tiene datos válidos dentro del área de interés.\n")
                f.write(f"Todos los pares de coherencia generados contenían únicamente\n")
                f.write(f"valores cero o NoData.\n")
                f.write(f"\n")
                f.write(f"RECOMENDACIÓN: Verificar otros subswaths disponibles o\n")
                f.write(f"considerar procesar con otra órbita/track.\n")
            
            logger.info(f"  Documentación: {info_file}")
            
        return True
        
    except Exception as e:
        logger.error(f"✗ Error eliminando subswath: {e}")
        return False


def print_summary(series_config, workspace, success, full_pipeline=False):
    """Imprime resumen final del procesamiento"""
    logger.info(f"\n{'=' * 80}")
    logger.info(f"RESUMEN DE PROCESAMIENTO")
    logger.info(f"{'=' * 80}\n")

    orbit = series_config['orbit_direction']
    subswath = series_config['subswath']

    logger.info(f"Serie: {orbit}-{subswath}")
    logger.info(f"Productos procesados: {series_config['total_products']}")
    logger.info(f"Workspace: {workspace['base']}")
    logger.info(f"Modo: {'PIPELINE COMPLETO' if full_pipeline else 'SOLO InSAR'}")
    logger.info("")

    if success:
        logger.info(f"Estado: ✓ COMPLETADO")
        logger.info("")
        logger.info("Resultados generados:")
        logger.info(f"  - Interferogramas InSAR short: {workspace['insar_short']}")
        logger.info(f"  - Interferogramas InSAR long: {workspace['insar_long']}")

        if full_pipeline:
            logger.info(f"  - Interferogramas recortados: {workspace['insar_cropped']}")

            # Verificar si hay productos polarimétricos
            pol_dir = workspace.get('polarimetry')
            if pol_dir and pol_dir.exists():
                pol_products = list(pol_dir.glob('*_HAAlpha.dim'))
                if pol_products:
                    logger.info(f"  - Productos polarimétricos: {workspace['polarimetry']}")
                    logger.info(f"  - Productos polarimétricos recortados: {workspace['polarimetry_cropped']}")

            logger.info("")
            logger.info(f"✓ Pipeline completo exitoso")
            logger.info("")
            logger.info("Próximos pasos:")
            logger.info("  1. Visualizar interferogramas en QGIS:")
            logger.info(f"     qgis {workspace['insar_cropped']}/*.tif")
            logger.info("  2. Visualizar polarimetría en QGIS:")
            logger.info(f"     qgis {workspace['polarimetry_cropped']}/*.tif")
            logger.info("  3. Comparar con el otro subswath (IW1 vs IW2)")
        else:
            logger.info("")
            logger.info("Para ejecutar el pipeline completo (InSAR+estadísticas), usa:")
            logger.info(f"  python3 process_insar_series.py {series_config.get('config_file', 'config.json')} --full-pipeline --skip-links")
    else:
        logger.info(f"Estado: ✗ ERROR")
        logger.info(f"  Revisa los logs en: {workspace['logs']}")

    logger.info("")
    logger.info(f"{'=' * 80}\n")


def run_polarimetric_processing(workspace, series_config, use_repository=False, save_to_repository=False):
    """
    Ejecuta descomposición H/A/Alpha para cada SLC de la serie.

    Args:
        workspace: Dict con rutas del workspace
        series_config: Configuración de la serie
        use_repository: Buscar productos en repositorio antes de procesar
        save_to_repository: Guardar productos al repositorio después de procesar
    """
    logger.info(f"\n{'=' * 80}")
    logger.info(f"PROCESAMIENTO POLARIMÉTRICO (H/A/Alpha)")
    logger.info(f"{'=' * 80}\n")

    # Usar directorio de salida polarimetría
    pol_dir = workspace['polarimetry']

    # Inicializar repositorio si está habilitado
    repository = None
    track_number = None

    if use_repository or save_to_repository:
        repository = InSARRepository()
        orbit_direction = series_config.get('orbit_direction', 'DESCENDING')
        subswath = series_config.get('subswath', 'IW1')
        logger.info(f"📦 Repositorio polarimetría habilitado: {orbit_direction} {subswath}")
        logger.info(f"   Buscando productos existentes antes de procesar...")

    # ESTRATEGIA DE BÚSQUEDA (jerarquía):
    # 1. Productos polarimétricos YA PROCESADOS en data/processed_products/
    # 2. SLC preprocesados en data/preprocessed_slc/ (para procesar polarimetría)
    # 3. SLC preprocesados locales en workspace['preprocessed']
    # 4. SLC originales (último recurso)
    
    # Extraer track primero (lo necesitamos para buscar en repositorio)
    track_number = None
    orbit_direction = series_config.get('orbit_direction', 'DESCENDING')
    subswath = series_config.get('subswath', 'IW1')
    
    # Detectar track desde productos SLC disponibles
    slc_links = list(workspace['slc'].glob('*.SAFE'))
    if repository and slc_links:
        track_number = repository.extract_track_from_slc(str(slc_links[0]))
        if track_number:
            logger.info(f"📡 Track detectado: {track_number}\n")
    
    # PASO 1: Buscar productos polarimétricos YA PROCESADOS en repositorio
    all_products_in_repo = False
    if repository and use_repository and track_number:
        logger.info(f"🔍 PASO 1: Verificando productos polarimétricos en repositorio...")
        
        try:
            repo_track_dir = repository.get_track_dir(orbit_direction, subswath, track_number)
            repo_pol_dir = repo_track_dir / "polarimetry"
            
            if repo_pol_dir.exists():
                # Contar cuántos productos polarimétricos hay en el repositorio
                repo_dates = [d.name for d in repo_pol_dir.iterdir() if d.is_dir()]
                slc_dates = [repository.extract_date_from_slc(str(slc)) for slc in slc_links]
                slc_dates = [d for d in slc_dates if d]  # Filtrar None
                
                # Verificar si todos los SLC tienen producto polarimétrico
                missing_dates = set(slc_dates) - set(repo_dates)
                
                if not missing_dates:
                    logger.info(f"   ✓ TODOS los productos polarimétricos están en repositorio ({len(slc_dates)} productos)")
                    logger.info(f"   → Creando symlinks desde repositorio...\n")
                    
                    # Crear symlinks para TODOS los productos
                    workspace['polarimetry'].mkdir(parents=True, exist_ok=True)
                    created = 0
                    
                    for date in slc_dates:
                        date_dir = repo_pol_dir / date
                        repo_products = list(date_dir.glob("*_HAAlpha.dim"))
                        
                        if repo_products:
                            repo_file = repo_products[0]
                            local_file = workspace['polarimetry'] / repo_file.name
                            
                            if not local_file.exists():
                                local_file.symlink_to(repo_file.absolute())
                                
                            # Symlink .data
                            repo_data = repo_file.with_suffix('.data')
                            local_data = local_file.with_suffix('.data')
                            if repo_data.exists() and not local_data.exists():
                                local_data.symlink_to(repo_data.absolute())
                            
                            created += 1
                    
                    logger.info(f"   ✓ {created} symlinks de productos polarimétricos creados")
                    all_products_in_repo = True
                    return True
                else:
                    logger.info(f"   ℹ️  {len(repo_dates)} productos en repositorio, {len(missing_dates)} faltantes")
                    logger.info(f"   → Procesamiento incremental necesario\n")
            else:
                logger.info(f"   ℹ️  Repositorio polarimetría no existe aún")
                logger.info(f"   → Procesamiento completo necesario\n")
        except Exception as e:
            logger.warning(f"   ⚠️  Error accediendo a repositorio: {e}\n")
    
    # PASO 2: Buscar SLC preprocesados en data/preprocessed_slc/ (caché global)
    input_dir = None
    products = []
    
    if repository and track_number:
        logger.info(f"🔍 PASO 2: Buscando SLC preprocesados en caché global...")
        
        try:
            orbit_suffix = "desc" if orbit_direction == "DESCENDING" else "asce"
            subswath_lower = subswath.lower()
            cache_dir = Path.cwd() / "data" / "preprocessed_slc" / f"{orbit_suffix}_{subswath_lower}" / f"t{track_number:03d}"
            
            if cache_dir.exists():
                # Buscar todos los productos .dim en subdirectorios de fecha
                cached_products = []
                for date_dir in cache_dir.iterdir():
                    if date_dir.is_dir():
                        cached_products.extend(date_dir.glob("*.dim"))
                
                if cached_products:
                    logger.info(f"   ✓ Encontrados {len(cached_products)} SLC preprocesados en caché global")
                    logger.info(f"   → Creando symlinks para polarimetría...\n")
                    
                    # Crear symlinks en workspace['preprocessed']
                    workspace['preprocessed'].mkdir(parents=True, exist_ok=True)
                    
                    for cached_slc in cached_products:
                        local_slc = workspace['preprocessed'] / cached_slc.name
                        if not local_slc.exists():
                            local_slc.symlink_to(cached_slc.absolute())
                        
                        # Symlink .data
                        cached_data = cached_slc.with_suffix('.data')
                        local_data = local_slc.with_suffix('.data')
                        if cached_data.exists() and not local_data.exists():
                            local_data.symlink_to(cached_data.absolute())
                    
                    input_dir = workspace['preprocessed']
                    products = list(input_dir.glob('*.dim'))
                    logger.info(f"   ✓ {len(products)} symlinks de SLC preprocesados creados")
                else:
                    logger.info(f"   ℹ️  Caché global existe pero sin productos")
            else:
                logger.info(f"   ℹ️  Caché global no existe: {cache_dir}")
        except Exception as e:
            logger.warning(f"   ⚠️  Error accediendo a caché global: {e}")
    
    # PASO 3: Buscar SLC preprocesados locales
    if not products:
        logger.info(f"\n🔍 PASO 3: Buscando SLC preprocesados locales...")
        input_dir = workspace['preprocessed']
        products = list(input_dir.glob('*.dim'))
        
        if products:
            logger.info(f"   ✓ Encontrados {len(products)} SLC preprocesados locales")
        else:
            logger.info(f"   ℹ️  No hay SLC preprocesados locales")
    
    # PASO 4: Último recurso - usar SLC originales
    if not products:
        logger.warning(f"\n⚠️  PASO 4: Usando SLC originales (último recurso)")
        logger.warning(f"   Nota: Esto requiere preprocesamiento y es menos eficiente")
        input_dir = workspace['slc']
        products = list(input_dir.glob('*.SAFE'))
        
        if products:
            logger.warning(f"   → {len(products)} SLC originales serán preprocesados\n")
        else:
            logger.error(f"   ✗ No se encontraron SLC originales")
            return False

    total = len(products)
    processed = 0
    failed = 0
    skipped_from_repo = 0
    
    logger.info(f"\n📋 Resumen: {total} productos a procesar para polarimetría\n")

    for idx, product in enumerate(products, 1):
        product_name = product.stem
        # Evitar procesar merged o archivos temporales
        if 'MERGED' in product_name: continue

        output_file = pol_dir / f"{product_name}_HAAlpha.dim"

        if output_file.exists():
            logger.info(f"[{idx}/{total}] ✓ Ya existe: {output_file.name}")
            processed += 1
            continue

        # Extraer fecha del producto SLC
        import re
        date_match = re.search(r'(\d{8})', product_name)
        product_date = date_match.group(1) if date_match else None

        # VERIFICAR REPOSITORIO ANTES DE PROCESAR
        if repository and use_repository and track_number and product_date:
            try:
                repo_track_dir = repository.get_track_dir(orbit_direction, subswath, track_number)
                repo_date_dir = repo_track_dir / "polarimetry" / product_date

                if repo_date_dir.exists():
                    # Buscar producto HAAlpha en el directorio de fecha
                    repo_products = list(repo_date_dir.glob("*_HAAlpha.dim"))

                    if repo_products:
                        repo_product_file = repo_products[0]
                        logger.info(f"[{idx}/{total}] 📦 Encontrado en repositorio: {product_date}")

                        # Crear symlink
                        try:
                            if not output_file.exists():
                                output_file.symlink_to(repo_product_file.absolute())
                            output_data = output_file.with_suffix('.data')
                            repo_data = repo_product_file.with_suffix('.data')
                            if repo_data.exists() and not output_data.exists():
                                output_data.symlink_to(repo_data.absolute())

                            logger.info(f"  ✓ Symlink creado desde repositorio")
                            skipped_from_repo += 1
                            processed += 1
                            continue
                        except Exception as e:
                            logger.warning(f"  ⚠️  Error creando symlink: {e} - procesando normalmente")
            except Exception as e:
                logger.debug(f"  Error consultando repositorio: {e}")

        logger.info(f"[{idx}/{total}] Procesando Polarimetría: {product_name}")

        try:
            # Detectar si el producto es preprocesado (.dim) o original (.SAFE)
            is_preprocessed = product.suffix == '.dim'

            if is_preprocessed:
                logger.debug(f"  → Producto preprocesado (.dim) - Skip Apply-Orbit-File")
            else:
                logger.debug(f"  → Producto original (.SAFE) - Apply-Orbit-File incluido")

            # 1. Generar XML
            xml_content = create_pol_decomposition_xml(str(product), str(output_file), is_preprocessed=is_preprocessed)
            
            # 2. Guardar XML temporal
            xml_path = pol_dir / "temp_pol.xml"
            with open(xml_path, 'w') as f:
                f.write(xml_content)
            
            # 3. Ejecutar GPT
            cmd = ['gpt', str(xml_path), '-c', '4G', '-q', '8'] # -q 8 usa 8 hilos
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info(f"  ✅ Éxito")

                # GUARDAR AL REPOSITORIO SI ESTÁ HABILITADO
                if repository and save_to_repository and track_number and product_date:
                    try:
                        repo_track_dir = repository.ensure_track_structure(orbit_direction, subswath, track_number)
                        dest_date_dir = repo_track_dir / "polarimetry" / product_date
                        dest_date_dir.mkdir(parents=True, exist_ok=True)
                        dest_file = dest_date_dir / output_file.name

                        if not dest_file.exists():
                            # Copiar .dim
                            shutil.copy2(output_file, dest_file)
                            # Copiar .data
                            output_data = output_file.with_suffix('.data')
                            if output_data.exists():
                                dest_data = dest_file.with_suffix('.data')
                                shutil.copytree(output_data, dest_data, dirs_exist_ok=True)

                            logger.info(f"  📦 Guardado en repositorio: track {track_number}/polarimetry/{product_date}/")

                            # OPTIMIZACIÓN: Reemplazar archivos locales por symlinks para ahorrar espacio
                            try:
                                # Verificar que la copia al repositorio fue exitosa
                                dest_data = dest_file.with_suffix('.data')
                                if dest_file.exists() and dest_data.exists():
                                    # Calcular tamaño para logging
                                    local_size_mb = output_file.stat().st_size / (1024 * 1024)

                                    # Eliminar archivos locales
                                    logger.debug(f"  🗑️  Eliminando producto local para ahorrar espacio...")
                                    local_data = output_file.with_suffix('.data')

                                    if output_file.exists() and not output_file.is_symlink():
                                        output_file.unlink()
                                        logger.debug(f"    ✓ Eliminado: {output_file.name}")

                                    if local_data.exists() and not local_data.is_symlink():
                                        shutil.rmtree(local_data)
                                        logger.debug(f"    ✓ Eliminado: {local_data.name}/")

                                    # Crear symlinks desde workspace → repositorio
                                    output_file.symlink_to(dest_file.absolute())
                                    local_data.symlink_to(dest_data.absolute())

                                    logger.info(f"  🔗 Symlinks creados: workspace → repositorio (~{local_size_mb:.0f} MB ahorrados)")
                                else:
                                    logger.warning(f"  ⚠️  Copia al repositorio incompleta - manteniendo archivos locales")
                            except Exception as e:
                                logger.warning(f"  ⚠️  Error creando symlinks: {e}")
                                logger.warning(f"  Producto guardado en repositorio pero duplicado en workspace")

                            # Actualizar metadata
                            metadata = repository.load_metadata(orbit_direction, subswath, track_number)
                            product_info = repository._extract_polarimetry_info(dest_file, product_date)
                            metadata['polarimetry_products'].append(product_info)
                            repository.save_metadata(orbit_direction, subswath, track_number, metadata)
                        else:
                            logger.debug(f"  Producto ya existe en repositorio")

                    except Exception as e:
                        logger.warning(f"  ⚠️  Error guardando al repositorio: {e}")

                processed += 1
            else:
                logger.error(f"  ❌ Fallo GPT: {result.stderr}")
                failed += 1
            
            # Limpiar
            if xml_path.exists(): xml_path.unlink()

        except Exception as e:
            logger.error(f"  ❌ Error script: {e}")
            failed += 1

    logger.info(f"\nResumen Polarimetría: {processed} OK, {failed} Fallidos.")
    if skipped_from_repo > 0:
        logger.info(f"  - Nuevos: {processed - skipped_from_repo}")
        logger.info(f"  - Desde repositorio: {skipped_from_repo}")
    logger.info(f"Salida en: {pol_dir}")

    # Retornar False si hubo fallos
    if failed > 0:
        logger.error(f"\n✗ Polarimetría completó con {failed} error(es)")
        return False

    return True


def get_existing_insar_pairs(repository, orbit_direction, subswath, track_number, series_dates=None):
    """
    Obtiene lista de pares InSAR ya existentes en el repositorio.

    Args:
        repository: Instancia de InSARRepository
        orbit_direction: ASCENDING o DESCENDING
        subswath: IW1, IW2, o IW3
        track_number: Número de track
        series_dates: Set opcional de fechas (YYYYMMDD) para filtrar pares de la serie

    Returns:
        dict: {'short': [(master, slave), ...], 'long': [(master, slave), ...]}
    """
    track_dir = repository.get_track_dir(orbit_direction, subswath, track_number)

    existing_pairs = {
        'short': [],
        'long': []
    }

    if not track_dir.exists():
        return existing_pairs

    # Leer metadata para obtener pares procesados
    try:
        metadata = repository.load_metadata(orbit_direction, subswath, track_number)

        for product in metadata.get('insar_products', []):
            master = product.get('master_date')
            slave = product.get('slave_date')
            pair_type = product.get('pair_type', 'short')  # CORRECCIÓN: era 'type', debe ser 'pair_type'

            if master and slave:
                # FILTRO: Solo incluir pares que estén dentro del periodo de la serie
                if series_dates is not None:
                    if master not in series_dates or slave not in series_dates:
                        continue  # Saltar pares fuera del periodo
                
                existing_pairs[pair_type].append((master, slave))

    except Exception as e:
        logger.debug(f"Error leyendo metadata del repositorio: {e}")

    return existing_pairs


def get_expected_insar_pairs(slc_dates, max_temporal_baseline=12):
    """
    Calcula qué pares InSAR deberían generarse según los SLC disponibles.

    Args:
        slc_dates: Lista de fechas SLC en formato YYYYMMDD (ordenadas)
        max_temporal_baseline: Días máximos para pares short (default: 12)

    Returns:
        dict: {'short': [(master, slave), ...], 'long': [(master, slave), ...]}
    """
    from datetime import datetime, timedelta

    expected_pairs = {
        'short': [],
        'long': []
    }

    if len(slc_dates) < 2:
        return expected_pairs

    # Convertir a datetime para cálculos
    date_objs = []
    for date_str in slc_dates:
        try:
            date_objs.append(datetime.strptime(date_str, '%Y%m%d'))
        except:
            logger.warning(f"Fecha inválida: {date_str}")
            continue

    # Pares SHORT: consecutivos (temporal baseline corta)
    for i in range(len(date_objs) - 1):
        master = date_objs[i].strftime('%Y%m%d')
        slave = date_objs[i + 1].strftime('%Y%m%d')
        expected_pairs['short'].append((master, slave))

    # Pares LONG: saltando 1 producto (mayor temporal baseline)
    for i in range(len(date_objs) - 2):
        master = date_objs[i].strftime('%Y%m%d')
        slave = date_objs[i + 2].strftime('%Y%m%d')
        expected_pairs['long'].append((master, slave))

    return expected_pairs


def get_missing_insar_pairs(existing_pairs, expected_pairs):
    """
    Identifica qué pares InSAR faltan por procesar.

    Args:
        existing_pairs: Pares ya existentes en repositorio
        expected_pairs: Pares esperados según SLC disponibles

    Returns:
        dict: {'short': [(master, slave), ...], 'long': [(master, slave), ...]}
    """
    missing_pairs = {
        'short': [],
        'long': []
    }

    # Convertir a sets para comparación eficiente
    for pair_type in ['short', 'long']:
        existing_set = set(existing_pairs.get(pair_type, []))
        expected_set = set(expected_pairs.get(pair_type, []))

        missing = expected_set - existing_set
        missing_pairs[pair_type] = sorted(list(missing))

    return missing_pairs


def get_required_slc_dates(missing_pairs):
    """
    Extrae todas las fechas SLC únicas necesarias para los pares faltantes.

    Args:
        missing_pairs: Dict con pares faltantes {'short': [(m,s), ...], 'long': [(m,s), ...]}

    Returns:
        set: Conjunto de fechas SLC únicas necesarias (formato YYYYMMDD)
    """
    required_dates = set()

    for pair_type in ['short', 'long']:
        for master, slave in missing_pairs.get(pair_type, []):
            required_dates.add(master)
            required_dates.add(slave)

    return required_dates


def check_missing_slcs(required_dates, slc_dir):
    """
    Verifica qué SLCs faltan en el directorio local.

    Args:
        required_dates: Set de fechas SLC necesarias (formato YYYYMMDD)
        slc_dir: Path al directorio de SLCs

    Returns:
        list: Lista de fechas SLC que faltan
    """
    missing_dates = []

    # Buscar productos SLC en el directorio
    slc_products = list(slc_dir.glob('S1*_IW_SLC__*.SAFE'))

    # Extraer fechas de los productos existentes
    from processing_utils import extract_date_from_filename
    existing_dates = set()
    for slc_path in slc_products:
        date_str = extract_date_from_filename(slc_path.name)
        if date_str:
            existing_dates.add(date_str[:8])  # YYYYMMDD

    # Identificar fechas faltantes
    for date in required_dates:
        if date not in existing_dates:
            missing_dates.append(date)

    return sorted(missing_dates)


def download_missing_slcs(missing_dates, series_config, workspace):
    """
    Descarga SLCs faltantes desde Copernicus.

    Args:
        missing_dates: Lista de fechas SLC a descargar (formato YYYYMMDD)
        series_config: Configuración de la serie (contiene AOI, orbit_direction, etc.)
        workspace: Dict con rutas del workspace

    Returns:
        bool: True si la descarga fue exitosa
    """
    if not missing_dates:
        return True

    logger.info(f"\n{'=' * 80}")
    logger.info(f"DESCARGA DE SLC FALTANTES PARA PROCESAMIENTO INCREMENTAL")
    logger.info(f"{'=' * 80}")
    logger.info(f"Fechas SLC a descargar: {len(missing_dates)}")
    for date in missing_dates:
        logger.info(f"  - {date}")
    logger.info("")

    # Preparar parámetros para download_copernicus.py
    import subprocess
    from pathlib import Path as PathLib

    # Leer AOI del config
    config_file = workspace['base'] / 'config.txt'
    aoi_wkt = None
    if config_file.exists():
        with open(config_file, 'r') as f:
            for line in f:
                if line.startswith('AOI='):
                    aoi_wkt = line.split('=', 1)[1].strip().strip('"')
                    break

    if not aoi_wkt:
        logger.error("No se encontró AOI en config.txt")
        return False

    # Construir comando de descarga
    project_root = PathLib.cwd()

    # Convertir fechas a formato YYYY-MM-DD para el script de descarga
    from datetime import datetime
    date_ranges = []
    for date_str in missing_dates:
        date_obj = datetime.strptime(date_str, '%Y%m%d')
        formatted_date = date_obj.strftime('%Y-%m-%d')
        # Descargar día específico (rango de 1 día)
        date_ranges.append(f"{formatted_date},{formatted_date}")

    # Ejecutar descarga para cada fecha
    orbit_direction = series_config.get('orbit_direction', 'DESCENDING')

    for date_range in date_ranges:
        cmd = [
            sys.executable,
            str(project_root / "scripts" / "download_copernicus.py"),
            "--collection", "SENTINEL-1",
            "--product-type", "SLC",
            "--aoi", aoi_wkt,
            "--date-range", date_range,
            "--orbit-direction", orbit_direction,
            "--auto-confirm"
        ]

        logger.info(f"Descargando: {date_range.split(',')[0]}")
        logger.info(f"Comando: {' '.join(cmd[:5])}...")

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=600  # 10 min por producto
            )

            if result.returncode == 0:
                logger.info(f"  ✓ Descarga exitosa")
            else:
                logger.warning(f"  ⚠️  Descarga falló (exit code: {result.returncode})")
                logger.warning(f"  Verifica los logs para más detalles")
                # Mostrar últimas líneas del output
                if result.stdout:
                    lines = result.stdout.strip().split('\n')
                    for line in lines[-5:]:
                        logger.warning(f"    {line}")

        except subprocess.TimeoutExpired:
            logger.error(f"  ✗ Timeout descargando {date_range}")
            return False
        except Exception as e:
            logger.error(f"  ✗ Error: {e}")
            return False

    logger.info(f"\n{'=' * 80}")
    logger.info(f"✓ DESCARGA DE SLC COMPLETADA")
    logger.info(f"{'=' * 80}\n")

    return True


def check_final_products_complete(output_dir, series_config):
    """
    Verifica si ya existen TODOS los productos finales esperados (cropped)
    
    Esta función calcula cuántos pares InSAR deberían existir según las fechas
    disponibles en la configuración y verifica que todos los archivos cropped
    finales ya estén presentes.
    
    Args:
        output_dir: Directorio de salida de la serie
        series_config: Configuración de la serie con productos/fechas
        
    Returns:
        tuple: (complete: bool, info: dict)
            - complete: True si todos los productos finales existen
            - info: Información sobre productos esperados vs existentes
    """
    output_path = Path(output_dir)
    cropped_dir = output_path / "insar" / "cropped"
    
    # Verificar primero productos intermedios (.dim) si no hay cropped
    if not cropped_dir.exists() or not any(cropped_dir.glob("Ifg_*_cropped.tif")):
        # Verificar si existen productos InSAR intermedios (.dim) sin recortar
        short_dir = output_path / "insar" / "short"
        long_dir = output_path / "insar" / "long"
        
        if short_dir.exists() or long_dir.exists():
            short_count = len(list(short_dir.glob("Ifg_*.dim"))) if short_dir.exists() else 0
            long_count = len(list(long_dir.glob("Ifg_*.dim"))) if long_dir.exists() else 0
            total_intermediate = short_count + long_count
            
            if total_intermediate > 0:
                logger.info(f"ℹ️  Productos InSAR intermedios encontrados: {total_intermediate}")
                logger.info(f"  Short: {short_count}, Long: {long_count}")
                logger.info(f"  → Solo falta recorte al AOI, NO es necesario reprocesar InSAR")
                
                # Retornar como no completo pero con info de intermedios
                return False, {
                    'expected': 0,
                    'existing': 0,
                    'intermediate_count': total_intermediate,
                    'message': 'Productos intermedios existen, solo falta recorte'
                }
    
    # Verificar que existe el directorio cropped
    if not cropped_dir.exists():
        return False, {
            'expected': 0,
            'existing': 0,
            'message': 'Directorio cropped no existe'
        }
    
    # Extraer fechas únicas de los productos en la configuración
    import re
    dates = set()
    for product in series_config.get('products', []):
        product_name = product.get('product', '')
        # Formato: S1A_IW_SLC__1SDV_20230111T060136_...
        match = re.search(r'_(\d{8})T\d{6}', product_name)
        if match:
            dates.add(match.group(1))
    
    dates = sorted(list(dates))
    
    if len(dates) < 2:
        return False, {
            'expected': 0,
            'existing': 0,
            'message': 'Menos de 2 fechas en configuración'
        }
    
    # Calcular pares esperados (consecutivos short + long)
    expected_pairs = []
    
    # Pares short (consecutivos)
    for i in range(len(dates) - 1):
        master = dates[i]
        slave = dates[i + 1]
        expected_pairs.append(f"Ifg_{master}_{slave}_cropped.tif")
    
    # Pares long (saltar 1, cada 3 fechas)
    for i in range(len(dates) - 2):
        master = dates[i]
        slave = dates[i + 2]
        expected_pairs.append(f"Ifg_{master}_{slave}_LONG_cropped.tif")
    
    # Verificar qué pares existen
    existing_files = set()
    for tif_file in cropped_dir.glob("Ifg_*_cropped.tif"):
        existing_files.add(tif_file.name)
    
    # Verificar completitud
    missing_pairs = []
    for expected in expected_pairs:
        if expected not in existing_files:
            missing_pairs.append(expected)
    
    complete = len(missing_pairs) == 0
    
    # Extraer fechas necesarias de los pares faltantes
    required_dates = set()
    if missing_pairs:
        import re
        for missing_pair in missing_pairs:
            # Formato: Ifg_20240704_20240716_cropped.tif o Ifg_20240704_20240716_LONG_cropped.tif
            match = re.search(r'Ifg_(\d{8})_(\d{8})', missing_pair)
            if match:
                master_date, slave_date = match.groups()
                required_dates.add(master_date)
                required_dates.add(slave_date)
    
    info = {
        'expected': len(expected_pairs),
        'existing': len(existing_files),
        'missing': len(missing_pairs),
        'dates': dates,
        'required_dates': sorted(list(required_dates)),  # Fechas SLC necesarias para pares faltantes
        'message': f'{len(existing_files)}/{len(expected_pairs)} productos finales'
    }
    
    if missing_pairs and len(missing_pairs) <= 10:
        info['missing_list'] = missing_pairs
    
    return complete, info


def check_and_use_repository_products(series_config, workspace, use_repository=False):
    """
    Verifica si los productos finales ya existen en el repositorio para este track.

    PROCESAMIENTO INCREMENTAL:
    - Si todos los pares esperados existen → usa repositorio, salta procesamiento
    - Si faltan algunos pares → retorna dict con pares faltantes para procesamiento incremental
    - Si no hay ningún par → retorna False para procesamiento completo

    Args:
        series_config: Configuración de la serie
        workspace: Dict con rutas del workspace
        use_repository: Si debe verificar el repositorio

    Returns:
        bool o dict:
          - True: Todos los pares existen, usar repositorio completo
          - False: No hay productos, procesar todo
          - dict: {'missing_pairs': {...}, 'track_number': N, 'existing_count': M}
                  Indica procesamiento incremental necesario
    """
    if not use_repository:
        return False

    logger.info(f"\n{'=' * 80}")
    logger.info(f"VERIFICACIÓN DE REPOSITORIO - PROCESAMIENTO INCREMENTAL")
    logger.info(f"{'=' * 80}\n")

    # Inicializar repositorio
    repository = InSARRepository()
    orbit_direction = series_config.get('orbit_direction', 'DESCENDING')
    subswath = series_config.get('subswath', 'IW1')

    # Buscar productos SLC desde los symlinks del workspace
    slc_dir = workspace.get('slc')
    if not slc_dir or not slc_dir.exists():
        logger.warning("No se encontró directorio SLC en workspace")
        return False

    # Obtener TODOS los productos SLC disponibles
    slc_products = sorted(list(slc_dir.glob("*.SAFE")))
    if not slc_products:
        logger.warning("No hay productos SLC en workspace")
        return False

    logger.info(f"SLC disponibles en workspace: {len(slc_products)}")

    # Extraer track del primer producto
    first_product = slc_products[0].name
    track_number = repository.extract_track_from_slc(first_product)

    if not track_number:
        logger.warning("No se pudo extraer track number, procesamiento normal")
        return False

    logger.info(f"Track detectado: t{track_number:03d}")
    logger.info(f"Órbita/Subswath: {orbit_direction} {subswath}")

    # Extraer fechas de SLC disponibles
    import re
    slc_dates = []
    for slc in slc_products:
        match = re.search(r'(\d{8})T\d{6}', slc.name)
        if match:
            slc_dates.append(match.group(1))

    slc_dates = sorted(list(set(slc_dates)))  # Eliminar duplicados y ordenar
    logger.info(f"Fechas SLC únicas: {len(slc_dates)}")
    if slc_dates:
        logger.info(f"  Rango: {slc_dates[0]} → {slc_dates[-1]}")

    # Obtener pares existentes en repositorio (FILTRADOS por fechas de la serie)
    existing_pairs = get_existing_insar_pairs(
        repository, orbit_direction, subswath, track_number, 
        series_dates=set(slc_dates)  # Filtrar solo pares dentro del periodo de la serie
    )
    existing_count = len(existing_pairs['short']) + len(existing_pairs['long'])

    logger.info(f"\nPares existentes en repositorio:")
    logger.info(f"  - Short: {len(existing_pairs['short'])}")
    logger.info(f"  - Long: {len(existing_pairs['long'])}")

    # Calcular pares esperados según SLC disponibles
    expected_pairs = get_expected_insar_pairs(slc_dates)
    expected_count = len(expected_pairs['short']) + len(expected_pairs['long'])

    logger.info(f"\nPares esperados según SLC disponibles:")
    logger.info(f"  - Short: {len(expected_pairs['short'])}")
    logger.info(f"  - Long: {len(expected_pairs['long'])}")

    # Identificar pares faltantes
    missing_pairs = get_missing_insar_pairs(existing_pairs, expected_pairs)
    missing_count = len(missing_pairs['short']) + len(missing_pairs['long'])

    logger.info(f"\nPares faltantes a procesar:")
    logger.info(f"  - Short: {len(missing_pairs['short'])}")
    logger.info(f"  - Long: {len(missing_pairs['long'])}")

    # DECISIÓN: ¿Qué hacer?
    if missing_count == 0:
        # CASO 1: Todos los pares ya existen
        logger.info(f"\n✓ REPOSITORIO COMPLETO")
        logger.info(f"  Todos los pares esperados ya existen")
        logger.info(f"  → Creando symlinks, SALTANDO procesamiento")

        # Crear symlinks a productos existentes
        track_dir = repository.get_track_dir(orbit_direction, subswath, track_number)
        short_products = list((track_dir / "insar" / "short").glob("*.dim"))
        long_products = list((track_dir / "insar" / "long").glob("*.dim"))

        # Crear symlinks short
        insar_short_dir = workspace['insar_short']
        for product in short_products:
            link_name = insar_short_dir / product.name
            if not link_name.exists():
                link_name.symlink_to(product.absolute())
            data_dir = product.parent / f"{product.stem}.data"
            if data_dir.exists():
                link_data = insar_short_dir / f"{product.stem}.data"
                if not link_data.exists():
                    link_data.symlink_to(data_dir.absolute())

        # Crear symlinks long
        insar_long_dir = workspace['insar_long']
        for product in long_products:
            link_name = insar_long_dir / product.name
            if not link_name.exists():
                link_name.symlink_to(product.absolute())
            data_dir = product.parent / f"{product.stem}.data"
            if data_dir.exists():
                link_data = insar_long_dir / f"{product.stem}.data"
                if not link_data.exists():
                    link_data.symlink_to(data_dir.absolute())

        logger.info(f"✓ Symlinks creados: {len(short_products) + len(long_products)} productos")
        return True

    elif existing_count == 0:
        # CASO 2: No hay ningún par procesado
        logger.info(f"\n→ REPOSITORIO VACÍO")
        logger.info(f"  No hay productos previos para este track")
        logger.info(f"  → Procesamiento completo desde cero")
        return False

    else:
        # CASO 3: Hay algunos pares, faltan otros → PROCESAMIENTO INCREMENTAL
        logger.info(f"\n⚙️  PROCESAMIENTO INCREMENTAL NECESARIO")
        logger.info(f"  Productos existentes: {existing_count}")
        logger.info(f"  Productos faltantes: {missing_count}")
        logger.info(f"  → Procesando solo los {missing_count} pares faltantes")

        # Retornar dict con información para procesamiento incremental
        return {
            'missing_pairs': missing_pairs,
            'existing_pairs': existing_pairs,
            'track_number': track_number,
            'existing_count': existing_count,
            'missing_count': missing_count,
            'repository': repository,
            'orbit_direction': orbit_direction,
            'subswath': subswath
        }


def run_preprocessing_incremental(workspace, config_file, required_slc_dates):
    """
    Ejecuta preprocesamiento SOLO de los SLCs necesarios para procesamiento incremental.

    Args:
        workspace: Dict con rutas del workspace
        config_file: Ruta al config.txt
        required_slc_dates: Set de fechas SLC necesarias (formato YYYYMMDD)

    Returns:
        bool: True si tuvo éxito
    """
    logger.info(f"Preprocesando solo SLCs necesarios para pares faltantes...")

    # Verificar si existe caché compartido
    from pathlib import Path
    import shutil

    project_base = workspace['base'].parent
    orbit_direction = None
    subswath = None

    # Leer orbit y subswath del config
    if config_file.exists():
        with open(config_file, 'r') as f:
            for line in f:
                if line.startswith('ORBIT_DIRECTION='):
                    orbit_direction = line.split('=')[1].strip().strip('"')
                elif line.startswith('SUBSWATH='):
                    subswath = line.split('=')[1].strip().strip('"').lower()

    if not orbit_direction or not subswath:
        logger.warning("No se pudo leer orbit/subswath del config")
        return False

    orbit_suffix = "desc" if orbit_direction == "DESCENDING" else "asce"
    shared_slc_dir = project_base / f"slc_preprocessed_{orbit_suffix}" / "products" / subswath

    # Verificar qué SLCs ya están preprocesados en caché compartido
    preprocessed_dates = set()
    if shared_slc_dir.exists():
        for dim_file in shared_slc_dir.glob("*.dim"):
            # Extraer fecha del nombre
            import re
            match = re.search(r'(\d{8})', dim_file.stem)
            if match:
                preprocessed_dates.add(match.group(1))

    # Identificar SLCs que necesitan preprocesamiento
    slcs_to_preprocess = required_slc_dates - preprocessed_dates

    if not slcs_to_preprocess:
        logger.info(f"✓ Todos los SLCs necesarios ya están preprocesados en caché")
        # Crear symlink al caché compartido
        series_slc_link = workspace['preprocessed']
        if series_slc_link.exists() and not series_slc_link.is_symlink():
            shutil.rmtree(series_slc_link)
        if not series_slc_link.exists():
            relative_path = os.path.relpath(shared_slc_dir, series_slc_link.parent)
            series_slc_link.symlink_to(relative_path)
            logger.info(f"  ✓ Symlink a caché: {series_slc_link.name} -> {relative_path}")
        return True

    logger.info(f"  SLCs en caché: {len(preprocessed_dates)}")
    logger.info(f"  SLCs a preprocesar: {len(slcs_to_preprocess)}")

    # Filtrar productos SLC del workspace que necesitan preprocesamiento
    slc_products_to_process = []
    for slc_path in workspace['slc'].glob('*.SAFE'):
        from processing_utils import extract_date_from_filename
        date_str = extract_date_from_filename(slc_path.name)
        if date_str and date_str[:8] in slcs_to_preprocess:
            slc_products_to_process.append(slc_path)

    if not slc_products_to_process:
        logger.warning("No se encontraron SLCs para preprocesar en workspace")
        return False

    logger.info(f"  Productos SLC a preprocesar: {len(slc_products_to_process)}")

    # Crear archivo temporal de configuración solo con los SLCs necesarios
    import tempfile
    temp_slc_dir = Path(tempfile.mkdtemp(prefix="slc_incremental_"))

    try:
        # Crear symlinks solo a SLCs necesarios
        for slc_path in slc_products_to_process:
            link_path = temp_slc_dir / slc_path.name
            if not link_path.exists():
                link_path.symlink_to(slc_path.absolute())

        # Crear config temporal apuntando al directorio filtrado
        temp_config = workspace['base'] / 'config_incremental.txt'
        with open(config_file, 'r') as f_in:
            with open(temp_config, 'w') as f_out:
                for line in f_in:
                    if line.startswith('SLC_DIR='):
                        f_out.write(f'SLC_DIR="{temp_slc_dir}"\n')
                    else:
                        f_out.write(line)

        # Ejecutar preprocesamiento con config temporal
        project_root = Path.cwd()
        cmd = [
            sys.executable,
            str(project_root / "scripts" / "preprocess_products.py"),
            "--slc",
            "--insar-mode",
            "--config", str(temp_config.name)
        ]

        logger.info(f"Ejecutando: {' '.join(cmd[:4])}...\n")

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(workspace['base'])
        )

        # Mostrar salida
        if result.stdout:
            lines = result.stdout.strip().split('\n')
            for line in lines[-20:]:
                logger.info(line)

        if result.returncode == 0:
            preprocessed_count = len(list(workspace['preprocessed'].glob('*.dim')))
            logger.info(f"\n✓ Preprocesamiento incremental completado")
            logger.info(f"  Productos preprocesados: {preprocessed_count}")
            return True
        else:
            logger.error(f"\n✗ Error en preprocesamiento incremental")
            return False

    finally:
        # Limpiar directorio temporal
        if temp_slc_dir.exists():
            shutil.rmtree(temp_slc_dir)
        if temp_config.exists():
            temp_config.unlink()


def run_insar_processing_incremental(workspace, config_file, series_config,
                                      missing_pairs, use_preprocessed=False,
                                      use_repository=False, save_to_repository=False):
    """
    Ejecuta procesamiento InSAR SOLO de los pares faltantes.

    Args:
        workspace: Dict con rutas del workspace
        config_file: Ruta al config.txt
        series_config: Configuración de la serie
        missing_pairs: Dict con pares faltantes {'short': [(m,s), ...], 'long': [(m,s), ...]}
        use_preprocessed: Si usar productos preprocesados
        use_repository: Buscar productos en repositorio
        save_to_repository: Guardar al repositorio

    Returns:
        bool: True si tuvo éxito
    """
    logger.info(f"Procesando solo pares faltantes...")

    orbit = series_config['orbit_direction']
    subswath = series_config['subswath']

    logger.info(f"  Serie: {orbit}-{subswath}")
    logger.info(f"  Pares short a procesar: {len(missing_pairs['short'])}")
    logger.info(f"  Pares long a procesar: {len(missing_pairs['long'])}")
    logger.info("")

    # Por ahora, delegamos al script normal de InSAR
    # El filtrado de pares se hace verificando si ya existen en workspace
    # (ya creamos symlinks a existentes, entonces process_insar_gpt los saltará)

    success = run_insar_processing(
        workspace,
        config_file,
        series_config,
        use_preprocessed=use_preprocessed,
        use_repository=use_repository,
        save_to_repository=save_to_repository
    )

    return success


def main():
    parser = argparse.ArgumentParser(
        description='Procesa una serie InSAR específica desde configuración JSON',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""

        """
    )

    parser.add_argument('config', help='Archivo JSON de configuración de la serie')
    parser.add_argument('--output', '-o', help='Directorio de salida (default: auto)')
    parser.add_argument('--skip-links', action='store_true',
                       help='Saltar creación de enlaces simbólicos (si ya existen)')
    parser.add_argument('--full-pipeline', action='store_true',
                       help='Ejecutar pipeline completo: InSAR + estadísticas')
    parser.add_argument('--insar-only', action='store_true',
                       help='Solo procesar InSAR (sin estadísticas)')
    parser.add_argument('--use-repository', action='store_true',
                       help='Buscar productos en repositorio compartido antes de procesar')
    parser.add_argument('--save-to-repository', action='store_true',
                       help='Guardar productos procesados al repositorio compartido')

    args = parser.parse_args()

    # Banner
    mode = "PIPELINE COMPLETO" if args.full_pipeline else "SOLO InSAR" if args.insar_only else "PIPELINE COMPLETO"

    series_config = load_series_config(args.config)

    if not series_config:
        return 1

    # Determinar directorio de salida
    if args.output:
        output_dir = args.output
    else:
        # Auto-generar nombre basado en órbita y sub-swath
        orbit = series_config['orbit_direction'].lower()[:4]
        subswath = series_config['subswath'].lower()
        output_dir = f"processing/insar_{orbit}_{subswath}"

    # Crear directorio base si no existe y configurar logger ANTES de workspace
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # LIMPIAR LOGS ANTIGUOS antes de iniciar nuevo procesamiento
    LoggerConfig.clean_series_logs(str(output_path))
    
    # Configurar logger DESPUÉS de limpiar
    global logger
    logger = LoggerConfig.setup_series_logger(
        series_dir=str(output_path),
        log_name="insar_processing"
    )
    
    logger.info(f"{'='*80}")
    logger.info(f"INICIO DE PROCESAMIENTO DE SERIE")
    logger.info(f"{'='*80}")
    logger.info(f"Configuración: {args.config}")
    logger.info(f"Directorio salida: {output_dir}")
    logger.info("")

    # VERIFICACIÓN TEMPRANA: ¿Ya están todos los productos finales?
    complete, info = check_final_products_complete(output_dir, series_config)
    
    # Guardar info para uso posterior (preprocesamiento selectivo)
    global final_products_info
    final_products_info = info
    
    if complete:
        logger.info(f"{'='*80}")
        logger.info(f"✓ SERIE YA COMPLETAMENTE PROCESADA")
        logger.info(f"{'='*80}")
        logger.info(f"Productos finales: {info['existing']}/{info['expected']} (100%)")
        logger.info(f"Fechas: {len(info['dates'])} ({info['dates'][0]} → {info['dates'][-1]})")
        logger.info(f"Directorio: {output_dir}/insar/cropped/")
        logger.info(f"")
        logger.info(f"→ SALTANDO TODO EL PROCESAMIENTO (ya completo)")
        logger.info(f"{'='*80}")
        return 0
    elif info.get('intermediate_count', 0) > 0:
        # Caso especial: existen productos InSAR intermedios pero no recortados
        logger.info(f"{'='*80}")
        logger.info(f"ℹ️  PRODUCTOS INSAR INTERMEDIOS YA EXISTEN")
        logger.info(f"{'='*80}")
        logger.info(f"Productos InSAR (.dim): {info['intermediate_count']}")
        logger.info(f"Estado: Procesamiento InSAR completo")
        logger.info(f"Falta: Solo recorte al AOI")
        logger.info(f"")
        logger.info(f"→ SALTANDO PREPROCESAMIENTO E INSAR")
        logger.info(f"→ Solo se ejecutará el recorte al AOI")
        logger.info(f"{'='*80}")
        logger.info(f"")
        
        # Crear workspace mínimo para recorte
        workspace = create_series_workspace(series_config, output_dir)
        config_file = create_config_file(series_config, workspace)
        
        # Ejecutar SOLO el recorte
        logger.info(f"\n{'=' * 80}")
        logger.info(f"PASO: RECORTE DE PRODUCTOS INSAR AL AOI")
        logger.info(f"{'=' * 80}\n")
        
        crop_success = run_insar_crop(workspace, series_config)
        
        if crop_success:
            logger.info(f"\n✓ Recorte completado exitosamente")
            logger.info(f"  Productos: {output_dir}/insar/cropped/")
        
        return 0 if crop_success else 1
    elif info['existing'] > 0:
        logger.info(f"{'='*80}")
        logger.info(f"⚠️  PROCESAMIENTO PARCIALMENTE COMPLETO")
        logger.info(f"{'='*80}")
        logger.info(f"Productos existentes: {info['existing']}/{info['expected']}")
        logger.info(f"Productos faltantes: {info['missing']}")
        if 'missing_list' in info:
            logger.info(f"Pares faltantes:")
            for missing in info['missing_list']:
                logger.info(f"  - {missing}")
        logger.info(f"")
        logger.info(f"Fechas SLC necesarias para completar: {len(info['required_dates'])}")
        if len(info['required_dates']) <= 10:
            logger.info(f"  {', '.join(info['required_dates'])}")
        logger.info(f"")
        logger.info(f"→ Solo se preprocesarán los {len(info['required_dates'])} SLCs necesarios")
        logger.info(f"{'='*80}")
        logger.info(f"")

    # Crear workspace (ahora logger ya existe)
    workspace = create_series_workspace(series_config, output_dir)

    # Crear enlaces simbólicos
    if not args.skip_links:
        link_count = create_symlinks(series_config, workspace)

        if link_count < 2:
            logger.error(f"\nERROR: Se necesitan al menos 2 productos para InSAR")
            logger.error(f"Enlaces creados: {link_count}")
            return 1
    else:
        logger.info(f"\n⚠️  Saltando creación de enlaces (--skip-links)")

    # Crear archivo de configuración
    config_file = create_config_file(series_config, workspace)

    # PASO 0: VALIDACIÓN TEMPRANA DE COBERTURA (antes de todo procesamiento)
    # Validar que el subswath cubre el AOI ANTES de verificar repositorio o procesar
    logger.info(f"\n{'=' * 80}")
    logger.info(f"VALIDACIÓN PREVIA DE COBERTURA")
    logger.info(f"{'=' * 80}\n")

    is_valid, coverage_pct, message = validate_subswath_coverage_before_processing(series_config, workspace)
    logger.info(f"  {message}")

    if not is_valid:
        logger.error(f"\n{'=' * 80}")
        logger.error(f"✗ VALIDACIÓN FALLIDA: Subswath sin cobertura suficiente del AOI")
        logger.error(f"{'=' * 80}")
        logger.error(f"  Cobertura detectada: {coverage_pct:.1f}%")
        logger.error(f"  Se requiere mínimo 10% de cobertura para procesar")
        logger.error(f"\nACCIÓN RECOMENDADA:")
        logger.error(f"  - Verificar que el AOI esté dentro del área de cobertura del subswath")
        logger.error(f"  - Considerar usar otro subswath (IW1 o IW2)")
        logger.error(f"  - Revisar la configuración JSON de la serie")
        logger.error(f"\n  → NO SE REALIZARÁ PROCESAMIENTO")
        logger.error(f"{'=' * 80}\n")
        return 2  # Exit code 2 = validación previa fallida

    # Nota: Esta advertencia ya no debería aparecer con el umbral de 75%
    # pero se mantiene para casos edge donde la validación pueda variar
    if coverage_pct > 0 and coverage_pct < 75:
        logger.warning(f"\n⚠️  ADVERTENCIA: Cobertura bajo el umbral recomendado ({coverage_pct:.1f}%)")
        logger.warning(f"  Se recomienda verificar si otro subswath tiene mejor cobertura")
        logger.warning(f"  Continuando con el procesamiento...\n")

    # PASO 1: VERIFICAR REPOSITORIO PRIMERO (ahorra 2-3 horas si productos ya existen)
    repo_check_result = check_and_use_repository_products(
        series_config,
        workspace,
        use_repository=args.use_repository
    )

    # Manejar 3 casos posibles: True, False, o dict (procesamiento incremental)
    if repo_check_result is True:
        # CASO 1: Repositorio completo, todos los pares existen
        using_repo_products = True
    elif isinstance(repo_check_result, dict):
        # CASO 2: Procesamiento incremental - hay pares faltantes
        logger.info(f"\n{'=' * 80}")
        logger.info(f"⚙️  PROCESAMIENTO INCREMENTAL ACTIVADO")
        logger.info(f"{'=' * 80}")
        logger.info(f"Pares faltantes a procesar: {repo_check_result['missing_count']}")
        logger.info(f"Pares existentes (reutilizar): {repo_check_result['existing_count']}")
        logger.info("")

        # 1. Crear symlinks a pares existentes
        logger.info("PASO 1: Creando symlinks a pares existentes en repositorio...")
        repository = repo_check_result['repository']
        orbit_direction = repo_check_result['orbit_direction']
        subswath = repo_check_result['subswath']
        track_number = repo_check_result['track_number']
        existing_pairs = repo_check_result['existing_pairs']

        track_dir = repository.get_track_dir(orbit_direction, subswath, track_number)

        # Crear symlinks para pares short existentes
        for master_date, slave_date in existing_pairs['short']:
            pair_name = f"Ifg_{master_date}_{slave_date}.dim"
            repo_file = track_dir / "insar" / "short" / pair_name
            local_file = workspace['insar_short'] / pair_name

            if repo_file.exists() and not local_file.exists():
                local_file.symlink_to(repo_file.absolute())
                # Symlink .data
                repo_data = repo_file.with_suffix('.data')
                local_data = local_file.with_suffix('.data')
                if repo_data.exists() and not local_data.exists():
                    local_data.symlink_to(repo_data.absolute())

        # Crear symlinks para pares long existentes
        for master_date, slave_date in existing_pairs['long']:
            pair_name = f"Ifg_{master_date}_{slave_date}_LONG.dim"
            repo_file = track_dir / "insar" / "long" / pair_name
            local_file = workspace['insar_long'] / pair_name

            if repo_file.exists() and not local_file.exists():
                local_file.symlink_to(repo_file.absolute())
                # Symlink .data
                repo_data = repo_file.with_suffix('.data')
                local_data = local_file.with_suffix('.data')
                if repo_data.exists() and not local_data.exists():
                    local_data.symlink_to(repo_data.absolute())

        logger.info(f"✓ Symlinks creados: {repo_check_result['existing_count']} pares")

        # 2. Identificar SLCs necesarios para pares faltantes
        missing_pairs = repo_check_result.get('missing_pairs', {'short': [], 'long': []})
        required_slc_dates = get_required_slc_dates(missing_pairs)

        logger.info(f"\nPASO 2: Verificando SLCs necesarios para {repo_check_result['missing_count']} pares faltantes...")
        logger.info(f"  SLCs únicos necesarios: {len(required_slc_dates)}")

        missing_slc_dates = check_missing_slcs(required_slc_dates, workspace['slc'])

        if missing_slc_dates:
            logger.warning(f"⚠️  Detectados {len(missing_slc_dates)} SLCs faltantes necesarios:")
            for date in missing_slc_dates:
                logger.warning(f"   - {date}")
            logger.warning("")

            # Intentar descargar SLCs faltantes
            logger.info(f"Descargando SLCs faltantes desde Copernicus...")
            download_success = download_missing_slcs(missing_slc_dates, series_config, workspace)

            if not download_success:
                logger.error(f"\n✗ Error descargando SLCs faltantes")
                logger.error(f"  No se puede continuar con procesamiento incremental")
                logger.error(f"  Verifica la conectividad y disponibilidad de productos en Copernicus")
                return 1

            logger.info(f"✓ SLCs faltantes descargados\n")
        else:
            logger.info(f"✓ Todos los SLCs necesarios están disponibles\n")

        # 3. Configurar para procesamiento incremental
        logger.info(f"PASO 3: Procesamiento incremental configurado")
        logger.info(f"  → Solo preprocesar {len(required_slc_dates)} SLCs necesarios")
        logger.info(f"  → Solo procesar {repo_check_result['missing_count']} pares nuevos")
        logger.info(f"  → Reutilizar {repo_check_result['existing_count']} pares existentes")
        logger.info(f"{'=' * 80}\n")

        # Marcar para procesamiento incremental (no usar todos los productos del repo)
        using_repo_products = 'incremental'
        incremental_info = {
            'required_slc_dates': required_slc_dates,
            'missing_pairs': missing_pairs,
            'repository': repository,
            'track_number': track_number
        }
    else:
        # CASO 3: Repositorio vacío o error
        using_repo_products = False

    if using_repo_products is True:
        logger.info(f"\n{'=' * 80}")
        logger.info(f"✓ PRODUCTOS OBTENIDOS DESDE REPOSITORIO")
        logger.info(f"{'=' * 80}")
        logger.info(f"Saltando:")
        logger.info(f"  - Validación de cobertura (ya verificada previamente)")
        logger.info(f"  - Pre-procesamiento SLC")
        logger.info(f"  - Procesamiento InSAR")
        logger.info(f"Continuando con:")
        logger.info(f"  - Búsqueda de productos polarimétricos en repositorio")
        logger.info(f"  - Recorte a AOI")
        logger.info(f"  - Cálculo de estadísticas")
        logger.info(f"{'=' * 80}\n")

        # Saltar directo a cropping y estadísticas
        # Marcar como exitoso para el flujo
        insar_success = True
        preprocessing_success = True

    elif using_repo_products == 'incremental':
        # PROCESAMIENTO INCREMENTAL: procesar solo lo necesario
        # NOTA: Validación de cobertura ya realizada al inicio

        # PASO 1: Verificar órbitas
        if not check_and_setup_orbits(workspace):
            logger.warning(f"⚠️  Advertencia en órbitas, pero continuando...")

        # PASO 2: Pre-procesamiento SOLO de SLCs necesarios
        logger.info(f"\n{'=' * 80}")
        logger.info(f"PASO 2: PRE-PROCESAMIENTO INCREMENTAL")
        logger.info(f"{'=' * 80}\n")
        logger.info(f"Solo preprocesar {len(incremental_info['required_slc_dates'])} SLCs necesarios")
        logger.info(f"(de {len(list(workspace['slc'].glob('*.SAFE')))} SLCs totales en workspace)\n")

        # Filtrar solo SLCs necesarios para preprocesamiento (usando función modificada)
        preprocessing_success = run_preprocessing(
            workspace,
            config_file,
            required_slc_dates=incremental_info['required_slc_dates']
        )

        # PASO 3: Procesamiento InSAR SOLO de pares faltantes (usando función modificada)
        logger.info(f"\n{'=' * 80}")
        logger.info(f"PASO 3: PROCESAMIENTO INSAR INCREMENTAL")
        logger.info(f"{'=' * 80}\n")
        logger.info(f"Solo procesar {len(incremental_info['missing_pairs']['short'])} pares short")
        logger.info(f"Solo procesar {len(incremental_info['missing_pairs']['long'])} pares long\n")

        # Convertir incremental_info al formato esperado por run_insar_processing
        missing_info_for_insar = {
            'all_exist': False,  # Si estamos aquí, hay pares faltantes
            'missing_count': len(incremental_info['missing_pairs']['short']) + len(incremental_info['missing_pairs']['long']),
            'existing_count': repo_check_result['existing_count'],
            'missing_pairs': [(m, s, 'short') for m, s in incremental_info['missing_pairs']['short']] +
                           [(m, s, 'long') for m, s in incremental_info['missing_pairs']['long']],
            'required_slc_dates': incremental_info['required_slc_dates']
        }

        insar_success = run_insar_processing(
            workspace,
            config_file,
            series_config,
            use_preprocessed=preprocessing_success,
            use_repository=args.use_repository,
            save_to_repository=args.save_to_repository,
            missing_info=missing_info_for_insar
        )

        if not insar_success:
            logger.error(f"✗ Error en procesamiento InSAR incremental - abortando")
            print_summary(series_config, workspace, False, args.full_pipeline)
            return 1

    else:
        # MODO NORMAL (sin repositorio)
        # NOTA: Validación de cobertura ya realizada al inicio

        # PASO 1: Verificar y preparar órbitas
        if not check_and_setup_orbits(workspace):
            logger.warning(f"⚠️  Advertencia en órbitas, pero continuando...")

        # PASO 2: Pre-procesamiento SLC
        # Verificar si existe SLC compartido a nivel de proyecto (según órbita)
        project_base = workspace['base'].parent  # processing/arenys_munt/
        subswath = series_config['subswath'].lower()  # iw1 o iw2 (validado previamente)
        orbit_direction = series_config.get('orbit_direction', 'DESCENDING')
        orbit_suffix = "desc" if orbit_direction == "DESCENDING" else "asce"
        shared_slc_dir = project_base / f"slc_preprocessed_{orbit_suffix}" / "products" / subswath

        if shared_slc_dir.exists() and list(shared_slc_dir.glob("*.dim")):
            logger.info(f"\n✓ Usando SLC pre-procesado compartido ({orbit_direction})")
            logger.info(f"  Directorio: {shared_slc_dir}")
            logger.info(f"  Sub-swath: {subswath.upper()}")
            logger.info(f"  Productos: {len(list(shared_slc_dir.glob('*.dim')))}")

            # Crear symlink a SLC preprocesados compartidos
            series_slc_link = workspace['preprocessed']

            # Si existe y es un directorio, eliminarlo para crear el symlink
            if series_slc_link.exists() and not series_slc_link.is_symlink():
                logger.warning(f"  ⚠️  Eliminando directorio local: {series_slc_link}")
                shutil.rmtree(series_slc_link)

            if not series_slc_link.exists():
                series_slc_link.parent.mkdir(parents=True, exist_ok=True)
                # Crear symlink relativo
                relative_path = os.path.relpath(shared_slc_dir, series_slc_link.parent)
                series_slc_link.symlink_to(relative_path)
                logger.info(f"  ✓ Symlink creado: {series_slc_link.name} -> {relative_path}")
            else:
                logger.info(f"  ✓ Symlink ya existe: {series_slc_link.name}")

            # Saltar pre-procesamiento (ya está hecho)
            preprocessing_success = True
        else:
            logger.warning(f"\n⚠️  No se encontró SLC compartido, pre-procesando para esta serie")
            # Cada serie preprocesa sus propios SLC en su workspace local
            
            # OPTIMIZACIÓN: Si ya hay productos parcialmente completos, usar preprocesamiento selectivo
            # Esto evita iterar sobre todos los SLCs cuando solo faltan algunos pares
            if 'final_products_info' in globals() and final_products_info.get('required_dates'):
                logger.info(f"\n🎯 PREPROCESAMIENTO SELECTIVO ACTIVADO")
                logger.info(f"  Solo se preprocesarán {len(final_products_info['required_dates'])} SLCs necesarios")
                logger.info(f"  (en lugar de verificar {len(list(workspace['slc'].glob('*.SAFE')))} SLCs totales)")
                logger.info("")
                
                preprocessing_success = run_preprocessing_incremental(
                    workspace,
                    config_file,
                    set(final_products_info['required_dates'])
                )
            else:
                # Preprocesamiento normal (todos los SLCs)
                preprocessing_success = run_preprocessing(workspace, config_file)

        # PASO 3: Procesamiento InSAR
        insar_success = run_insar_processing(
            workspace,
            config_file,
            series_config,
            use_preprocessed=preprocessing_success,
            use_repository=args.use_repository,
            save_to_repository=args.save_to_repository
        )

        if not insar_success:
            logger.error(f"✗ Error en procesamiento InSAR - abortando")
            print_summary(series_config, workspace, False, args.full_pipeline)
            return 1

    # Si solo queremos InSAR, terminar aquí
    if args.insar_only:
        print_summary(series_config, workspace, True, False)
        return 0

    # PIPELINE COMPLETO: Continuar con recorte, estadísticas y fusión
    if args.full_pipeline:
        logger.info(f"\n{'=' * 80}")
        logger.info(f"PIPELINE COMPLETO: Estadísticas y fusión (SLC-only)")
        logger.info(f"{'=' * 80}\n")

        pol_success = run_polarimetric_processing(
            workspace,
            series_config,
            use_repository=args.use_repository,
            save_to_repository=args.save_to_repository
        )

        # PASO 4: Recorte productos InSAR al AOI
        crop_success = run_insar_crop(workspace, series_config)
        # El crop no es crítico, continuar incluso si falla

        # PASO 4.5: Recorte productos polarimétricos al AOI
        if pol_success:
            pol_crop_success = run_polarimetric_crop(workspace, series_config)
            # El crop no es crítico, continuar incluso si falla

        # PASO 5: Estadísticas temporales (DEPRECATED - pairs/ ya no se usa)
        # La coherencia está disponible directamente en los productos InSAR
        # stats_success = run_statistics(workspace, series_config)

        # PASO 6: Validar cobertura real del subswath (verificación posterior)
        # NOTA: Esta es una verificación adicional post-procesamiento
        # La validación previa (PASO 0) ya debería haber detectado problemas de cobertura
        is_valid, valid_pairs, total_pairs, avg_coverage = validate_subswath_coverage(workspace)
        
        if not is_valid:
            logger.warning(f"\n{'=' * 80}")
            logger.warning(f"⚠️  ADVERTENCIA: VALIDACIÓN POSTERIOR DETECTÓ PROBLEMAS DE COBERTURA")
            logger.warning(f"{'=' * 80}")
            logger.warning(f"  Cobertura promedio en productos InSAR: {avg_coverage:.2f}%")
            logger.warning(f"  Pares válidos: {valid_pairs}/{total_pairs}")
            logger.warning(f"\n  Nota: La validación previa indicó cobertura suficiente,")
            logger.warning(f"        pero los productos generados tienen baja cobertura real.")
            logger.warning(f"        Esto puede indicar problemas en el procesamiento o bursts sin datos.")
            logger.warning(f"{'=' * 80}\n")
            
            # No eliminar - solo advertir. Los productos pueden ser útiles para análisis.

        # PASO 6: Limpieza de archivos intermedios
        logger.info(f"\n{'=' * 80}")
        logger.info(f"PASO 6: LIMPIEZA DE ARCHIVOS INTERMEDIOS")
        logger.info(f"{'=' * 80}\n")
        
        cleanup_intermediate_files(workspace, series_config)

        # Todo completado exitosamente
        print_summary(series_config, workspace, True, args.full_pipeline)
        return 0
    else:
        # Por defecto, solo InSAR (para compatibilidad con ejecuciones anteriores)
        print_summary(series_config, workspace, True, False)
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        if logger:
            logger.warning(f"\n\n⚠️  Interrumpido por el usuario")
        else:
            logger.info(f"\n\n⚠️  Interrumpido por el usuario")
        sys.exit(130)
    except Exception as e:
        if logger:
            logger.error(f"\nERROR: {str(e)}", exc_info=True)
        else:
            logger.info(f"\nERROR: {str(e)}")
            import traceback
            traceback.print_exc()
        sys.exit(1)
