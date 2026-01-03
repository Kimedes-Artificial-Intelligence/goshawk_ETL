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

            if coverage < 10.0:
                return (False, coverage,
                        f"Subswath {subswath} tiene solo {coverage:.1f}% de cobertura del AOI")
            elif coverage < 50.0:
                return (True, coverage,
                        f"⚠️ Subswath {subswath} tiene cobertura parcial ({coverage:.1f}%)")
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


def run_preprocessing(workspace, config_file):
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

    Returns:
        bool: True si el pre-procesamiento fue exitoso
    """
    logger.info(f"\n{'=' * 80}")
    logger.info(f"PASO 2: PRE-PROCESAMIENTO (TOPSAR-Split + Subset AOI)")
    logger.info(f"{'=' * 80}\n")

    # Verificar si ya existen productos pre-procesados
    existing_preprocessed = list(workspace['preprocessed'].glob('*.dim'))
    if len(existing_preprocessed) >= 1:
        logger.info(f"ℹ️  Ya existen {len(existing_preprocessed)} productos preprocesados")
        logger.info(f"   → Procesamiento incremental: solo se preprocesarán nuevos SLC\n")

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

    logger.info(f"Ejecutando: {' '.join(cmd)}\n")

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(workspace['base'])  # Ejecutar desde el workspace para que las rutas relativas funcionen
    )

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
                         use_repository=False, save_to_repository=False):
    """
    Ejecuta el procesamiento InSAR para la serie

    Args:
        workspace: Diccionario con rutas del workspace
        config_file: Ruta al archivo de configuración
        series_config: Configuración de la serie
        use_preprocessed: Si usar productos pre-procesados
        use_repository: Buscar productos en repositorio antes de procesar
        save_to_repository: Guardar productos al repositorio después de procesar

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
            
            # Ejecutar preprocesamiento
            preprocessing_success = run_preprocessing(workspace, config_file)
            
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
    """
    base_dir = workspace['base']
    subswath = series_config.get('subswath', 'unknown')
    orbit = series_config.get('orbit_direction', 'unknown')
    

    
    logger.info(f"\nEliminando subswath inválido...")
    logger.info(f"  Directorio: {base_dir}")
    
    try:
        # Eliminar todo el directorio del subswath (workspace local)
        if os.path.exists(base_dir):
            shutil.rmtree(base_dir)
            logger.info(f"✓ Subswath eliminado")
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
    logger.info(f"PASO EXTRA: DESCOMPOSICIÓN POLARIMÉTRICA (H/A/Alpha)")
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

    # Buscar productos SLC (usamos los preprocesados .dim si existen, si no los links SLC)
    # NOTA: Para polarimetría necesitamos las bandas complejas (i_VV, q_VV, i_VH, q_VH).
    # Los preprocesados de InSAR (--insar-mode) tienen esto.
    input_dir = workspace['preprocessed']
    products = list(input_dir.glob('*.dim'))
    
    if not products:
        logger.warning("No hay productos preprocesados. Intentando con SLC originales...")
        input_dir = workspace['slc']
        products = list(input_dir.glob('*.SAFE'))

    total = len(products)
    processed = 0
    failed = 0
    skipped_from_repo = 0

    # Extraer track del primer producto si usamos repositorio
    if repository and track_number is None and len(products) > 0:
        track_number = repository.extract_track_from_slc(str(products[0]))
        if track_number:
            logger.info(f"📡 Track detectado: {track_number}\n")
        else:
            logger.warning("⚠️  No se pudo detectar track - deshabilitando repositorio\n")
            repository = None

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
            # 1. Generar XML
            xml_content = create_pol_decomposition_xml(str(product), str(output_file))
            
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


def get_existing_insar_pairs(repository, orbit_direction, subswath, track_number):
    """
    Obtiene lista de pares InSAR ya existentes en el repositorio.

    Args:
        repository: Instancia de InSARRepository
        orbit_direction: ASCENDING o DESCENDING
        subswath: IW1, IW2, o IW3
        track_number: Número de track

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
            pair_type = product.get('type', 'short')  # short o long

            if master and slave:
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

    # Obtener pares existentes en repositorio
    existing_pairs = get_existing_insar_pairs(repository, orbit_direction, subswath, track_number)
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
    
    # Configurar logger ANTES de crear workspace
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

    # PASO 0: VERIFICAR REPOSITORIO PRIMERO (ahorra 2-3 horas si productos ya existen)
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
        logger.warning(f"\n{'=' * 80}")
        logger.warning(f"⚠️  PROCESAMIENTO INCREMENTAL DETECTADO")
        logger.warning(f"{'=' * 80}")
        logger.warning(f"Pares faltantes: {repo_check_result['missing_count']}")
        logger.warning(f"Pares existentes: {repo_check_result['existing_count']}")
        logger.warning("")

        # NUEVO: Verificar si faltan SLCs para los pares faltantes
        missing_pairs = repo_check_result.get('missing_pairs', {'short': [], 'long': []})
        required_slc_dates = get_required_slc_dates(missing_pairs)
        missing_slc_dates = check_missing_slcs(required_slc_dates, workspace['slc'])

        if missing_slc_dates:
            logger.warning(f"⚠️  Detectados {len(missing_slc_dates)} SLCs faltantes necesarios para pares:")
            for date in missing_slc_dates:
                logger.warning(f"   - {date}")
            logger.warning("")

            # Intentar descargar SLCs faltantes
            logger.warning(f"Intentando descargar SLCs faltantes desde Copernicus...")
            download_success = download_missing_slcs(missing_slc_dates, series_config, workspace)

            if not download_success:
                logger.error(f"\n✗ Error descargando SLCs faltantes")
                logger.error(f"  No se puede continuar con procesamiento incremental")
                logger.error(f"  Verifica la conectividad y disponibilidad de productos en Copernicus")
                return 1

            logger.info(f"✓ SLCs faltantes descargados correctamente\n")
        else:
            logger.info(f"✓ Todos los SLCs necesarios ya están disponibles localmente\n")

        logger.warning(f"Se procesará TODO desde cero y se actualizará el repositorio.")
        logger.warning(f"Los pares existentes se mantendrán, los nuevos se añadirán.")
        logger.warning(f"{'=' * 80}\n")

        # Por ahora, procesar TODO (futuro: solo faltantes)
        using_repo_products = False
    else:
        # CASO 3: Repositorio vacío o error
        using_repo_products = False

    if using_repo_products:
        logger.info(f"\n{'=' * 80}")
        logger.info(f"✓ PRODUCTOS OBTENIDOS DESDE REPOSITORIO")
        logger.info(f"{'=' * 80}")
        logger.info(f"Saltando:")
        logger.info(f"  - Validación de cobertura (ya verificada previamente)")
        logger.info(f"  - Pre-procesamiento SLC")
        logger.info(f"  - Procesamiento InSAR")
        logger.info(f"  - Procesamiento polarimétrico")
        logger.info(f"Continuando con:")
        logger.info(f"  - Recorte a AOI")
        logger.info(f"  - Cálculo de estadísticas")
        logger.info(f"{'=' * 80}\n")

        # Saltar directo a cropping y estadísticas
        # Marcar como exitoso para el flujo
        insar_success = True
        preprocessing_success = True

    else:
        # PASO 0: Validación previa de cobertura (ANTES de procesar)
        # OPTIMIZACIÓN: Evita procesar si el subswath no cubre el AOI
        logger.info(f"\n{'=' * 80}")
        logger.info(f"PASO 0: VALIDACIÓN PREVIA DE COBERTURA")
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
            return 2  # Exit code 2 = validación previa fallida

        if coverage_pct > 0 and coverage_pct < 50:
            logger.warning(f"\n⚠️  ADVERTENCIA: Cobertura parcial ({coverage_pct:.1f}%)")
            logger.warning(f"  Se recomienda verificar si otro subswath tiene mejor cobertura")
            logger.warning(f"  Continuando con el procesamiento...")

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

        # PASO 6: Validar cobertura real del subswath
        is_valid, valid_pairs, total_pairs, avg_coverage = validate_subswath_coverage(workspace)
        
        if not is_valid:
            logger.warning(f"\n{'=' * 80}")
            logger.warning(f"SUBSWATH SIN COBERTURA DEL AOI")
            logger.warning(f"{'=' * 80}")
            
            # Eliminar subswath inválido
            cleanup_invalid_subswath(workspace, series_config)
            
            logger.error(f"\nEl procesamiento se detuvo porque el subswath no cubre el AOI")
            logger.error(f"Cobertura promedio: {avg_coverage:.2f}%")
            logger.error(f"Pares válidos: {valid_pairs}/{total_pairs}")
            
            return 2  # Exit code 2 = subswath sin cobertura

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
