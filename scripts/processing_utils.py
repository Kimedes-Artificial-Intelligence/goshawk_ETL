#!/usr/bin/env python3
"""
Utilidades comunes para scripts de procesamiento GPT
"""

import os
import re
import glob
import logging

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_config(config_file="../config.txt"):
    """Cargar configuración desde config.txt"""
    config = {
        'SLC_DIR': 'data/sentinel1_slc',
        'GRD_DIR': 'data/sentinel1_grd',
        'PREPROCESSED_SLC_DIR': 'data/preprocessed_slc',
        'OUTPUT_DIR': 'processed',
        'AOI': None
    }

    # Buscar config.txt en varios lugares
    config_paths = [
        config_file,
        'config.txt',
        '../config.txt',
        os.path.join(os.path.dirname(__file__), '..', 'config.txt')
    ]

    config_found = False
    for path in config_paths:
        if os.path.exists(path):
            config_file = path
            config_found = True
            break

    if not config_found:
        logger.warning(f"No se encontró config.txt, usando valores por defecto")
        return config

    with open(config_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                # Remover comentarios inline
                if '#' in value:
                    value = value.split('#')[0]
                config[key.strip()] = value.strip().strip('"')

    return config


def extract_date_from_filename(filename):
    """
    Extrae la fecha del nombre del archivo Sentinel-1 (original o pre-procesado)

    Soporta:
    - Formato original: S1A_IW_SLC__1SDV_20240901T055322_...
    - Formato pre-procesado: GRD_20240901_subset.dim o SLC_20240901_subset.dim
    - Formato fusionado: S1X_IW_GRD_MERGED_20250927.dim o S1X_IW_SLC_MERGED_20250910.dim
    """
    # Formato fusionado: S1X_IW_XXX_MERGED_YYYYMMDD.dim
    match = re.search(r'MERGED_(\d{8})', filename)
    if match:
        return match.group(1) + 'T000000'

    # Formato original: YYYYMMDDTHHMMSS
    match = re.search(r'(\d{8}T\d{6})', filename)
    if match:
        return match.group(1)

    # Formato pre-procesado: GRD_YYYYMMDD_subset.dim o SLC_YYYYMMDD_subset.dim
    match = re.search(r'(?:GRD|SLC)_(\d{8})_subset', filename)
    if match:
        return match.group(1) + 'T000000'

    return None


def find_slc_products(slc_dir, use_preprocessed=False):
    """
    Encuentra productos SLC en el directorio especificado

    Args:
        slc_dir: Directorio donde buscar productos
        use_preprocessed: Si True, busca archivos .dim; si False, busca .SAFE

    Returns:
        Lista de rutas a productos SLC
    """
    if not os.path.isdir(slc_dir):
        logger.error(f"No existe el directorio: {slc_dir}")
        return []

    if use_preprocessed:
        # Buscar archivos .dim
        pattern = os.path.join(slc_dir, '*.dim')
        products = glob.glob(pattern)
        logger.info(f"Buscando pre-procesados en: {pattern}")
    else:
        # Buscar directorios .SAFE
        pattern = os.path.join(slc_dir, '*.SAFE')
        products = glob.glob(pattern)
        # Si no hay .SAFE, buscar .zip
        if not products:
            pattern = os.path.join(slc_dir, '*.zip')
            products = glob.glob(pattern)
        logger.info(f"Buscando originales en: {pattern}")

    # Filtrar solo productos SLC
    slc_products = [p for p in products if 'SLC' in os.path.basename(p)]

    # Ordenar por fecha
    slc_products.sort()

    return slc_products


def find_grd_products(grd_dir, use_preprocessed=False):
    """
    Encuentra productos GRD en el directorio especificado

    Args:
        grd_dir: Directorio donde buscar productos
        use_preprocessed: Si True, busca archivos .dim; si False, busca .SAFE

    Returns:
        Lista de rutas a productos GRD
    """
    if not os.path.isdir(grd_dir):
        logger.error(f"No existe el directorio: {grd_dir}")
        return []

    if use_preprocessed:
        # Buscar archivos .dim
        pattern = os.path.join(grd_dir, '*.dim')
        products = glob.glob(pattern)
        logger.info(f"Buscando pre-procesados en: {pattern}")
    else:
        # Buscar directorios .SAFE
        pattern = os.path.join(grd_dir, '*.SAFE')
        products = glob.glob(pattern)
        # Si no hay .SAFE, buscar .zip
        if not products:
            pattern = os.path.join(grd_dir, '*.zip')
            products = glob.glob(pattern)
        logger.info(f"Buscando originales en: {pattern}")

    # Filtrar solo productos GRD
    grd_products = [p for p in products if 'GRD' in os.path.basename(p)]

    # Ordenar por fecha
    grd_products.sort()

    return grd_products


def validate_product(product_path, use_preprocessed=False):
    """
    Valida que un producto sea válido

    Args:
        product_path: Ruta al producto
        use_preprocessed: Si True, valida .dim; si False, valida .SAFE

    Returns:
        bool: True si el producto es válido
    """
    if not os.path.exists(product_path):
        logger.error(f"No existe: {product_path}")
        return False

    if use_preprocessed:
        # Para .dim, verificar que exista el archivo .dim y el directorio .data
        if not product_path.endswith('.dim'):
            logger.error(f"No es un archivo .dim: {product_path}")
            return False

        data_dir = product_path.replace('.dim', '.data')
        if not os.path.isdir(data_dir):
            logger.error(f"No existe directorio .data: {data_dir}")
            return False

        return True
    else:
        # Para .SAFE, verificar que exista manifest.safe
        if product_path.endswith('.SAFE'):
            manifest = os.path.join(product_path, 'manifest.safe')
            if not os.path.exists(manifest):
                logger.error(f"No se encuentra manifest.safe en {product_path}")
                return False
        # Para .zip, asumir que es válido (se validará al descomprimir)

        return True


def extract_orbit_from_manifest(product_path):
    """
    Extrae la dirección de órbita (ASCENDING/DESCENDING) del manifest.safe

    Args:
        product_path: Ruta al producto .SAFE o directorio que lo contiene

    Returns:
        str: 'ASCENDING' o 'DESCENDING', o None si no se puede determinar
    """
    import xml.etree.ElementTree as ET
    from pathlib import Path

    # Convertir a Path para manejo más fácil
    product_path = Path(product_path)

    # Determinar ruta al manifest.safe
    if product_path.is_dir() and product_path.suffix == '.SAFE':
        manifest_path = product_path / 'manifest.safe'
    elif product_path.name == 'manifest.safe':
        manifest_path = product_path
    else:
        # Intentar buscar manifest.safe dentro
        manifest_path = product_path / 'manifest.safe'

    if not manifest_path.exists():
        logger.warning(f"No se encuentra manifest.safe en {product_path}")
        return None

    try:
        # Parsear XML
        tree = ET.parse(manifest_path)
        root = tree.getroot()

        # Buscar elemento <s1:pass>
        # Namespace de Sentinel-1
        namespaces = {
            's1': 'http://www.esa.int/safe/sentinel-1.0',
            'safe': 'http://www.esa.int/safe/sentinel/1.1'
        }

        # Buscar en orbitProperties
        for elem in root.iter():
            if elem.tag.endswith('pass'):
                orbit_direction = elem.text.strip()
                if orbit_direction in ['ASCENDING', 'DESCENDING']:
                    logger.debug(f"Órbita detectada: {orbit_direction} - {product_path.name}")
                    return orbit_direction

        logger.warning(f"No se encontró información de órbita en {manifest_path}")
        return None

    except ET.ParseError as e:
        logger.error(f"Error parseando manifest.safe: {e}")
        return None
    except Exception as e:
        logger.error(f"Error extrayendo órbita de {manifest_path}: {e}")
        return None
