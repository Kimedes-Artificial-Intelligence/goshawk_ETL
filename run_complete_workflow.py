#!/usr/bin/env python3
"""
Script: run_complete_workflow.py
Descripción: Gestor maestro del workflow completo de datos espaciales

Este script gestiona el proceso desde la selección de AOI y fechas
hasta el procesamiento final, incluyendo:
1. Selección de AOI
2. Selección de fechas
3. Descarga de productos Sentinel-1
4. Descarga de órbitas
5. Creación de proyecto AOI
6. Generación de configuraciones
7. Procesamiento completo con repositorio compartido automático

IMPORTANTE: El sistema de repositorio compartido está SIEMPRE ACTIVO.
Los productos InSAR y polarimétricos se organizan por track en:
  data/processed_products/{orbit}_{subswath}/t{track:03d}/

Esto permite:
- Reutilización automática de productos entre proyectos del mismo track
- Ahorro de tiempo y espacio (symlinks en lugar de duplicados)
- Trazabilidad completa con metadata por track

Uso:
  python run_complete_workflow.py
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Importar sistema de logging centralizado y utilidades comunes
sys.path.append(os.path.join(os.path.dirname(__file__), 'scripts'))
from scripts.logging_utils import LoggerConfig

logger = None  # Se configurará después de seleccionar AOI


def list_available_aois():
    """
    Lista los archivos AOI disponibles en el directorio aoi/

    Returns:
        list: Lista de rutas a archivos .geojson
    """
    aoi_dir = Path("aoi")
    if not aoi_dir.exists():
        logger.warning(f"No existe el directorio aoi/")
        return []

    aoi_files = list(aoi_dir.glob("*.geojson"))
    return sorted(aoi_files)


def select_aoi_interactive():
    """
    Permite al usuario seleccionar un AOI de forma interactiva
    Soporta búsqueda por número o por nombre

    Returns:
        Path: Ruta al archivo GeoJSON seleccionado
    """
    aoi_files = list_available_aois()

    if not aoi_files:
        print(f"✗ No hay archivos AOI disponibles en aoi/")
        print(f"Crea un archivo GeoJSON en aoi/ primero")
        return None

    # Crear diccionario con info de cada AOI
    aoi_info = []
    for aoi_file in aoi_files:
        try:
            with open(aoi_file, 'r') as f:
                data = json.load(f)
                name = data['features'][0]['properties'].get('name', aoi_file.stem)
                area = data['features'][0]['properties'].get('area', 'N/A')
        except:
            name = aoi_file.stem
            area = 'N/A'
        
        aoi_info.append({
            'file': aoi_file,
            'name': name,
            'area': area,
            'filename': aoi_file.name
        })

    # Mostrar listado en formato tabla
    print(f"{'='*80}")
    print(f"AOI DISPONIBLES")
    print(f"{'='*80}")
    
    # Encabezado de tabla
    print(f"{'#':<4} {'Nombre':<35} {'Área':<20} {'Archivo':<20}")
    print(f"{'-'*80}")
    
    for i, info in enumerate(aoi_info, 1):
        # Truncar nombre y archivo si son muy largos
        name_display = info['name'][:33] + '..' if len(info['name']) > 35 else info['name']
        filename_display = info['filename'][:18] + '..' if len(info['filename']) > 20 else info['filename']
        
        print(f"{i:<4} {name_display:<35} {info['area']:<20} {filename_display:<20}")
    
    print(f"{'-'*80}")

    # Mostrar ayuda
    print(f"💡 Puedes seleccionar por:")
    print(f"  • Número (ej: 1, 2, 3...)")
    print(f"  • Nombre (ej: barcelona, madrid...)")
    print(f"  • 'list' para ver lista de nuevo")
    print(f"  • 'q' o Ctrl+C para cancelar")

    while True:
        try:
            selection = input(f"Selecciona AOI: ").strip()
            
            # Comando especiales
            if selection.lower() in ['q', 'quit', 'exit']:
                print(f"Cancelado por el usuario")
                return None
            
            if selection.lower() == 'list':
                # Reimprimir lista
                print()
                for i, info in enumerate(aoi_info, 1):
                    name_display = info['name'][:33] + '..' if len(info['name']) > 35 else info['name']
                    filename_display = info['filename'][:18] + '..' if len(info['filename']) > 20 else info['filename']
                    print(f"{i:<4} {name_display:<35} {info['area']:<20} {filename_display:<20}")
                print()
                continue
            
            # Intentar como número primero
            try:
                idx = int(selection) - 1
                if 0 <= idx < len(aoi_info):
                    selected = aoi_info[idx]
                    print(f"✓ AOI seleccionado: {selected['name']}")
                    print(f"  Archivo: {selected['filename']}")
                    print(f"  Área: {selected['area']}")
                    return selected['file']
                else:
                    print(f"✗ Número fuera de rango [1-{len(aoi_info)}]")
                    continue
            except ValueError:
                # No es un número, buscar por nombre
                search_term = selection.lower()
                matches = []
                
                for i, info in enumerate(aoi_info):
                    # Buscar en nombre, filename
                    if (search_term in info['name'].lower() or 
                        search_term in info['filename'].lower() or
                        search_term in info['file'].stem.lower()):
                        matches.append((i, info))
                
                if len(matches) == 0:
                    print(f"✗ No se encontró ningún AOI con '{selection}'")
                    print(f"  Prueba con: número, nombre parcial, o 'list' para ver todos")
                elif len(matches) == 1:
                    # Una sola coincidencia, seleccionar automáticamente
                    idx, selected = matches[0]
                    print(f"✓ AOI encontrado y seleccionado: {selected['name']}")
                    print(f"  Archivo: {selected['filename']}")
                    print(f"  Área: {selected['area']}")
                    return selected['file']
                else:
                    # Múltiples coincidencias, mostrar y pedir que elija
                    print(f"Se encontraron {len(matches)} coincidencias:")
                    for idx, info in matches:
                        print(f"  {idx+1}. {info['name']} ({info['filename']})")
                    print(f"Por favor, especifica el número o un nombre más exacto")
                    
        except KeyboardInterrupt:
            print(f"Cancelado por el usuario")
            return None


def select_date_range_interactive():
    """
    Permite al usuario seleccionar rango de fechas de forma interactiva

    Returns:
        tuple: (start_date, end_date) como strings YYYY-MM-DD
    """
    logger.info(f"Selección de Rango de Fechas:")

    # Opciones predefinidas

    logger.info("Opciones rápidas:")
    logger.info("  1. Últimos 3 meses")
    logger.info("  2. Últimos 6 meses")
    logger.info("  3. Último año")
    logger.info("  4. Personalizado")
    logger.info("  q. Cancelar")

    while True:
        try:
            option = input(f"Selecciona opción [1-4]: ").strip()

            end_date = datetime.now()

            if option == "1":
                start_date = end_date - timedelta(days=90)
            elif option == "2":
                start_date = end_date - timedelta(days=180)
            elif option == "3":
                start_date = end_date - timedelta(days=365)
            elif option == "4":
                # Fechas personalizadas
                start_str = input(f"Fecha inicio (YYYY-MM-DD): ").strip()
                end_str = input(f"Fecha fin (YYYY-MM-DD): ").strip()

                try:
                    start_date = datetime.strptime(start_str, '%Y-%m-%d')
                    end_date = datetime.strptime(end_str, '%Y-%m-%d')
                except ValueError:
                    logger.info(f"✗ Formato de fecha inválido")
                    continue
            elif option.lower() in ['q', 'quit', 'exit']:
                logger.info(f"Cancelado por el usuario")
                return None, None
            else:
                logger.info(f"✗ Opción inválida")
                continue

            # Validar rango
            if start_date >= end_date:
                logger.info(f"✗ La fecha de inicio debe ser anterior a la fecha fin")
                continue

            start_str = start_date.strftime('%Y-%m-%d')
            end_str = end_date.strftime('%Y-%m-%d')

            logger.info(f"✓ Rango seleccionado: {start_str} a {end_str}")
            logger.info(f"  ({(end_date - start_date).days} días)")

            return start_str, end_str

        except KeyboardInterrupt:
            logger.info(f"Cancelado por el usuario")
            return None, None


def check_project_exists(project_name):
    """
    Verifica si ya existe un proyecto para este AOI con estructura completa

    Args:
        project_name: Nombre del proyecto

    Returns:
        bool: True si existe con estructura mínima
    """
    project_dir = Path("processing") / project_name
    aoi_file = project_dir / "aoi.geojson"
    
    # Verificar que existe el directorio Y el archivo aoi.geojson
    return project_dir.exists() and aoi_file.exists()


def download_products(workflow_config):
    """
    Descarga productos Sentinel-1 para el AOI y rango de fechas

    Args:
        worlflow_config: Configuración del workflow con parámetros de descarga
    Returns:
        bool: True si exitoso
    """

    logger.info(f"{'=' * 80}")
    logger.info(f"PASO 2: DESCARGA DE PRODUCTOS {workflow_config.get('products_to_download', {}).get('sentinel_1', 'SLC')}")
    logger.info(f"{'=' * 80}")
    logger.info(f"Filtrando productos con 100% de cobertura del AOI")

    # Asegurar que existe el directorio de logs del proyecto
    project_dir = Path("processing") / workflow_config["project_name"]
    log_dir = project_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Construir comando base con todos los parámetros
    product_type = workflow_config.get('products_to_download', {}).get('sentinel_1', 'SLC')
    cmd = [
        sys.executable,
        "scripts/download_copernicus.py",
        "--aoi-geojson", workflow_config["aoi_file"],
        "--start-date", workflow_config["start_date"],
        "--end-date", workflow_config["end_date"],
        "--yes",  # Autoconfirmar descarga si hay espacio suficiente
        "--min-coverage", "100.0",  # Solo productos con 100% de cobertura del AOI
        "--product-type", product_type,
        "--satellites", *workflow_config["satellites"],  # Unpacking: ["S1A", "S1C"] -> "S1A" "S1C"
        "--orbit_type", workflow_config["orbit_type"],  # Underscore, not hyphen
        "--min-coverage", str(workflow_config["min_coverage"]),
        "--log-dir", str(log_dir),
        "--skip-processed"  # Omitir productos ya procesados en el repositorio
    ]

    # Descargar ambas órbitas en llamadas separadas
    logger.info(f"Descargando AMBAS direcciones de órbita: ASCENDING + DESCENDING")
    
    for orbit in workflow_config["orbit_direction"]:
        orbit_cmd = cmd + ["--orbit-direction", orbit]
        logger.info(f"Comando ({orbit}): {' '.join(orbit_cmd)}")
        success = True
        try:
            result = subprocess.run(
                orbit_cmd,
                check=False,
                text=True
            )
            
            if result.returncode != 0:
                logger.warning(f"Algunos productos {product_type} {orbit} no se descargaron")
                success = False
            else:
                logger.info(f"✓ Productos {product_type} {orbit} descargados")
        except Exception as e:
            logger.error(f"✗ Error en descarga de productos {product_type} {orbit}: {e}")
            success = False
    
    return success


def download_orbits(worlflow_config) -> bool:
    """
    Descarga archivos de órbitas

    Args:
        worlflow_config:

    Returns:
        bool: True si exitoso
    """
    logger.info(f"{'=' * 80}")
    logger.info(f"PASO 1: DESCARGA DE ÓRBITAS")
    logger.info(f"{'=' * 80}")

    # Asegurar que existe el directorio de logs del proyecto
    log_dir = Path("processing") / worlflow_config["project_name"]
    log_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "scripts/download_orbits.py",
        "--start-date", worlflow_config["start_date"],
        "--end-date", worlflow_config["end_date"],
        "--orbit_type", worlflow_config["orbit_type"],
        "--satellite", *worlflow_config["satellites"],
        "--log-dir", str(log_dir)
    ]

    logger.info(f"Comando: {' '.join(cmd)}")

    try:
        # Ejecutar y capturar salida para diagnóstico
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=60 * 60  # ajustar timeout según necesidades
        )

        # Registrar stdout/stderr para depuración
        if result.stdout:
            logger.debug(result.stdout)
        if result.stderr:
            logger.debug(result.stderr)

        if result.returncode == 0:
            logger.info(f" Órbitas descargadas")
            return True
        else:
            logger.warning(f" El script devolvió exit code {result.returncode}")
            if result.stderr:
                logger.warning(f"stderr: {result.stderr}")
            return False

    except subprocess.TimeoutExpired as e:
        logger.error(f"Timeout descargando órbitas: {e}")
        return False
    except Exception as e:
        logger.error(f"✗ Error ejecutando `scripts/download_orbits.py`: {e}")
        return False


def create_aoi_project(aoi_file, project_name, log=None):
    """
    Crea la estructura del proyecto AOI

    Args:
        aoi_file: Path al archivo GeoJSON
        project_name: Nombre del proyecto
        log: Logger a usar (opcional, usa el global si no se especifica)

    Returns:
        bool: True si exitoso
    """
    logger.info(f"{'=' * 80}")
    logger.info(f"PASO 3: CREAR PROYECTO AOI")
    logger.info(f"{'=' * 80}")

    if check_project_exists(project_name):
        logger.info(f"✓ Proyecto ya existe (continuando)")
        return True

    # Determinar directorio de logs
    project_dir = Path("processing") / project_name
    log_dir = project_dir / "logs"

    cmd = [
        sys.executable,
        "scripts/create_aoi_project.py",
        str(aoi_file),
        "--name", project_name,
        "--log-dir", str(log_dir)
    ]

    logger.info(f"Comando: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            check=True,
            text=True
        )
        if result.returncode == 0:
            return True
    except Exception as e:
        logger.error(f"✗ Error creando proyecto AOI: {e}")
        return False


def generate_product_configurations(workflow_config, orbit_direction):
    """
    Genera configuraciones de productos para el proyecto usando select_multiswath_series.py

    Args:
        orbit_direction: DESCENDING o ASCENDING
        workflow_config: Configuración del workflow
    Returns:
        bool: True si exitoso
    """
    logger.info(f"{'=' * 80}")
    logger.info(f"PASO 4: GENERAR CONFIGURACIONES DE PRODUCTOS ({orbit_direction})")
    logger.info(f"{'=' * 80}")

    project_dir = Path("processing") / workflow_config["project_name"]
    project_aoi = Path(workflow_config["aoi_file"])
    
    # Determinar prefijo según órbita
    orbit_prefix = "desc" if orbit_direction == "DESCENDING" else "asce"

    # Verificar si ya existen configuraciones para esta órbita
    config_pattern = f"selected_products_{orbit_prefix}_*.json"
    config_files = list(project_dir.glob(config_pattern))

    if len(config_files) >= 3:
        logger.info(f"✓ Ya existen configuraciones {orbit_direction} ({len(config_files)} archivos)")
        for cf in config_files:
            logger.info(f"  - {cf.name}")
        return True

    # Ejecutar select_multiswath_series.py
    slc_dir = Path("data") / "sentinel1_slc"

    if not slc_dir.exists() or not list(slc_dir.glob("*.SAFE")):
        logger.error(f"✗ No se encontraron productos SLC en {slc_dir}")
        return False

    logger.info(f"Ejecutando select_multiswath_series.py para {orbit_direction}...")
    logger.info(f"  Directorio SLC: {slc_dir}")
    logger.info(f"  AOI: {project_aoi}")
    logger.info(f"  Salida: {project_dir}")
    logger.info(f"  Órbita: {orbit_direction}")
    logger.info(f"  Período: {workflow_config['start_date']} a {workflow_config['end_date']}")
    logger.info(f"  Satélites: {', '.join(workflow_config['satellites'])}")
    logger.info("")

    # Determinar directorio de logs
    log_dir = project_dir / "logs"

    cmd = [
        sys.executable,
        "scripts/select_multiswath_series.py",
        "--data-dir", str(slc_dir),
        "--aoi-geojson", str(project_aoi),
        "--output-dir", str(project_dir),
        "--orbit-direction", orbit_direction,
        "--start-date", workflow_config['start_date'],
        "--end-date", workflow_config['end_date'],
        "--satellites", *workflow_config['satellites'],
        "--log-dir", str(log_dir)
    ]

    try:
        result = subprocess.run(
            cmd,
            check=False,
            text=True,
            capture_output=False
        )

        # Verificar que se crearon los archivos
        config_files = list(project_dir.glob(config_pattern))
        if len(config_files) >= 3:
            logger.info(f"✓ Configuraciones {workflow_config['orbit_direction']} generadas: {len(config_files)} archivos")
            for cf in config_files:
                logger.info(f"  ✓ {cf.name}")
            return True
        else:
            logger.warning(f"Solo se generaron {len(config_files)} configuraciones {workflow_config['orbit_direction']}")
            return len(config_files) > 0

    except Exception as e:
        logger.error(f"✗ Error generando configuraciones {workflow_config['orbit_direction']}: {e}")
        return False


def process_series(project_name, config_json, series_name, use_repository=True, save_to_repository=True):
    """
    Procesa una serie InSAR completa (coherencia + estadísticas)

    NOTA: El repositorio compartido está siempre activo por defecto.
    Los productos se buscan primero en el repositorio (ahorro de tiempo)
    y se guardan automáticamente después del procesamiento (para futuros proyectos).

    Args:
        project_name: Nombre del proyecto
        config_json: Path al archivo JSON de configuración
        series_name: Nombre de la serie (e.g., 'insar_desc_iw1')
        use_repository: Buscar productos en repositorio antes de procesar (default: True)
        save_to_repository: Guardar productos al repositorio después de procesar (default: True)

    Returns:
        bool: True si exitoso, False si falló (incluyendo sin cobertura para fallback)
    """
    project_dir = Path("processing") / project_name
    output_dir = project_dir / series_name

    logger.info(f"{'~' * 80}")
    logger.info(f"PROCESANDO SERIE: {series_name}")
    logger.info(f"{'~' * 80}")

    # Verificar si ya está procesado (verificar productos finales recortados)
    cropped_dir = output_dir / "fusion" / "insar" / "cropped"
    if cropped_dir.exists():
        cropped_files = list(cropped_dir.glob("Ifg_*_cropped.tif"))
        if len(cropped_files) > 0:
            logger.info(f"✓ Serie ya procesada ({len(cropped_files)} productos finales), saltando...")
            return True

    # Ejecutar process_insar_series.py con --full-pipeline
    cmd = [
        sys.executable,
        "scripts/process_insar_series.py",
        str(config_json),
        "--output", str(output_dir),
        "--full-pipeline"
    ]

    if use_repository:
        cmd.append("--use-repository")
        logger.info(f"  → Verificando repositorio compartido antes de procesar")

    if save_to_repository:
        cmd.append("--save-to-repository")
        logger.info(f"  → Guardando productos al repositorio compartido")

    logger.info(f"Comando: {' '.join(cmd)}")
    logger.info(f"⏳ Este proceso puede tardar varias horas...")

    try:
        result = subprocess.run(
            cmd,
            check=False,
            text=True
        )

        if result.returncode == 0:
            logger.info(f"✓ Serie {series_name} completada exitosamente")
            return True
        elif result.returncode == 2:
            # Exit code 2 = subswath sin cobertura
            logger.warning(f"Serie {series_name} sin cobertura del AOI")
            logger.warning(f"  El subswath no tiene datos válidos dentro del área de interés")
            # Retornar False para activar fallback a siguiente IW
            return False
        else:
            # Exit code 1 u otro = Error en procesamiento
            logger.error(f"✗ Serie {series_name} falló (exit code {result.returncode})")
            logger.error(f"  Revisar log para más detalles")
            # Retornar False para intentar siguiente IW como fallback
            return False

    except Exception as e:
        logger.error(f"✗ Error procesando serie {series_name}: {e}")
        return False


def evaluate_orbit_processing_quality(project_name, orbit_direction, log=None):
    """
    Evalúa la calidad/completitud del procesamiento de una órbita
    
    Verifica:
    - Si se generaron series procesadas
    - Cuántos productos finales se obtuvieron
    - Si hay pares con estadísticas completas
    
    Args:
        project_name: Nombre del proyecto
        orbit_direction: DESCENDING o ASCENDING
        log: Logger
        
    Returns:
        dict: {
            'success': bool,  # True si al menos una IW procesó exitosamente
            'complete': bool,  # True si tiene productos completos (coherencia)
            'series_count': int,  # Número de series procesadas
            'pairs_count': int,  # Número de pares con datos
            'details': str  # Descripción del resultado
        }
    """
    _logger = log if log is not None else logger
    project_dir = Path("processing") / project_name
    orbit_prefix = "desc" if orbit_direction == "DESCENDING" else "asce"

    result = {
        'success': False,
        'complete': False,
        'series_count': 0,
        'pairs_count': 0,
        'details': ''
    }

    # Buscar series procesadas para esta órbita
    series_dirs = list(project_dir.glob(f"insar_{orbit_prefix}_iw*"))
    result['series_count'] = len(series_dirs)

    if len(series_dirs) == 0:
        result['details'] = f"No se generaron series para {orbit_direction}"
        return result

    # Para cada serie, contar productos finales recortados
    total_products = 0

    for series_dir in series_dirs:
        # Buscar productos finales en fusion/insar/cropped/ (igual que process_series)
        cropped_dir = series_dir / "fusion" / "insar" / "cropped"
        if not cropped_dir.exists():
            continue

        cropped_files = list(cropped_dir.glob("Ifg_*_cropped.tif"))
        total_products += len(cropped_files)

    result['pairs_count'] = total_products  # Mantener nombre para compatibilidad

    if total_products > 0:
        result['success'] = True
        result['complete'] = True
        result['details'] = f"{len(series_dirs)} series, {total_products} productos"
    else:
        result['details'] = f"{len(series_dirs)} series pero sin productos válidos"
    
    return result


def run_processing(project_name, orbit_direction, use_repository=True, save_to_repository=True):
    """
    Ejecuta el procesamiento completo del proyecto para una órbita específica

    NOTA: El repositorio compartido está siempre activo por defecto.

    Args:
        project_name: Nombre del proyecto
        orbit_direction: Dirección de órbita (DESCENDING o ASCENDING)
        use_repository: Buscar productos en repositorio antes de procesar (default: True)
        save_to_repository: Guardar productos al repositorio después de procesar (default: True)

    Returns:
        bool: True si exitoso
    """
    logger.info(f"{'=' * 80}")
    logger.info(f"PASO 5: PROCESAMIENTO COMPLETO ({orbit_direction})")
    logger.info(f"{'=' * 80}")

    project_dir = Path("processing") / project_name
    orbit_prefix = "desc" if orbit_direction == "DESCENDING" else "asce"

    # Buscar archivos de configuración para esta órbita
    config_pattern = f"selected_products_{orbit_prefix}_*.json"
    config_files = sorted(project_dir.glob(config_pattern))

    if not config_files:
        logger.error(f"✗ No se encontraron configuraciones {orbit_direction} en {project_dir}")
        return False

    logger.info(f"Configuraciones encontradas: {len(config_files)}")
    for cf in config_files:
        logger.info(f"  - {cf.name}")
    logger.info("")

    # ESTRATEGIA REPOSITORIO: Procesar TODAS las IW disponibles
    # Cada IW se guarda al repositorio para reutilización futura
    # Beneficio: Otros proyectos pueden usar cualquier combinación de IWs

    logger.info(f"Estrategia: Procesar TODAS las subswaths disponibles (IW1, IW2, IW3)")
    logger.info(f"Cada subswath se guarda al repositorio compartido para reutilización")
    logger.info("")

    success_count = 0
    failed_count = 0
    processed_iws = []  # IWs que se procesaron exitosamente
    failed_iws = []     # IWs sin cobertura del AOI

    # Ordenar configs por prioridad: IW1 → IW2 → IW3 (para logging ordenado)
    iw_priority = {'iw1': 1, 'iw2': 2, 'iw3': 3}
    config_files_sorted = sorted(config_files, key=lambda cf: iw_priority.get(
        cf.stem.replace("selected_products_", "").split('_')[-1].lower(), 99
    ))

    for config_file in config_files_sorted:
        # Extraer nombre de serie del archivo
        series_suffix = config_file.stem.replace("selected_products_", "")  # desc_iw1
        series_name = f"insar_{series_suffix}"  # insar_desc_iw1
        iw_num = series_suffix.split('_')[-1].upper()  # IW1, IW2, IW3

        logger.info(f"→ Procesando {iw_num}...")

        # Procesar serie
        result = process_series(project_name, config_file, series_name,
                               use_repository=use_repository,
                               save_to_repository=save_to_repository)

        if result:
            # IW procesada exitosamente
            success_count += 1
            processed_iws.append(iw_num)
            logger.info(f"✓ {iw_num} procesada y guardada al repositorio")
        else:
            # IW falló (sin cobertura o error)
            failed_count += 1
            failed_iws.append(iw_num)
            logger.warning(f"✗ {iw_num} sin cobertura del AOI o error en procesamiento")

        logger.info("")  # Línea en blanco entre IWs

    # Resumen
    logger.info(f"{'=' * 80}")
    logger.info(f"RESUMEN PROCESAMIENTO {orbit_direction}")
    logger.info(f"{'=' * 80}")
    logger.info(f"Total subswaths disponibles: {len(config_files_sorted)}")
    logger.info(f"Procesadas exitosamente: {success_count}")
    logger.info(f"Sin cobertura/fallidas: {failed_count}")

    if processed_iws:
        iw_list = ", ".join(processed_iws)
        logger.info(f"✓ IWs guardadas al repositorio: {iw_list}")

    if failed_iws:
        failed_list = ", ".join(failed_iws)
        logger.info(f"✗ IWs sin cobertura: {failed_list}")

    logger.info(f"{'=' * 80}")

    # Retornar éxito si al menos una IW se procesó
    if success_count > 0:
        logger.info(f"✓ PROCESAMIENTO {orbit_direction} EXITOSO")
        return True
    else:
        logger.error(f"✗ NINGUNA IW TIENE COBERTURA DEL AOI")
        return False


def print_summary(project_name, success, orbit_results=None, log=None):
    """
    Imprime resumen final del workflow

    Args:
        project_name: Nombre del proyecto
        success: Si el workflow fue exitoso
        orbit_results: Dict con resultados de cada órbita procesada
        log: Logger a usar (opcional, usa el global si no se especifica)
    """
    _logger = log if log is not None else logger
    project_dir = Path("processing") / project_name

    _logger.info(f"{'=' * 80}")
    _logger.info(f"RESUMEN DEL WORKFLOW")
    _logger.info(f"{'=' * 80}")

    _logger.info(f"Proyecto: {project_name}")
    _logger.info(f"Directorio: {project_dir}")
    _logger.info("")

    if success:
        _logger.info(f"Estado: ✓ WORKFLOW COMPLETADO")
        _logger.info("")
        
        # Mostrar resultados por órbita si disponible
        if orbit_results:
            _logger.info(f"📊 RESULTADOS POR ÓRBITA:")
            for orbit, quality in orbit_results.items():
                if quality:
                    status_icon = "✓" if quality['complete'] else "⚠"
                    _logger.info(f"  {status_icon} {orbit}:")
                    _logger.info(f"     {quality['details']}")
                    if not quality['complete']:
                        _logger.info(f"     → Productos parciales (usar como referencia)")
            _logger.info("")
        
        _logger.info("Resultados finales:")

        # Buscar series individuales
        desc_series = sorted(project_dir.glob("insar_desc_iw*"))
        asc_series = sorted(project_dir.glob("insar_asce_iw*"))

        if desc_series or asc_series:
            _logger.info(f"  SERIES PROCESADAS:")
            if desc_series:
                _logger.info(f"    DESCENDING: {len(desc_series)} series")
                for s in desc_series:
                    _logger.info(f"      - {s.name}")
            if asc_series:
                _logger.info(f"    ASCENDING: {len(asc_series)} series")
                for s in asc_series:
                    _logger.info(f"      - {s.name}")

        _logger.info("")
        _logger.info(f"Visualización de estadísticas en QGIS:")

        if desc_series or asc_series:
            _logger.info(f"  # Coherencia media:")
            _logger.info(f"  qgis {project_dir}/insar_*/fusion/coherence_mean.tif &")
            _logger.info(f"  # Variabilidad VV:")
            _logger.info(f"  qgis {project_dir}/insar_*/fusion/vv_std.tif &")
            _logger.info(f"  # Entropía media:")
            _logger.info(f"  qgis {project_dir}/insar_*/fusion/entropy_mean.tif &")

        _logger.info("")
        _logger.info(f"Documentación:")
        _logger.info(f"  cat {project_dir}/README.md")
    else:
        _logger.info(f"Estado: ✗ ERROR EN WORKFLOW")
        _logger.info("")
        _logger.info("Revisar logs en:")
        _logger.info(f"  {project_dir}/insar_*/logs/")

    _logger.info("")
    _logger.info(f"{'=' * 80}")


def clean_project_directory(project_dir):
    """
    Elimina el directorio del proyecto existente de forma segura
    
    Args:
        project_dir: Path al directorio del proyecto
    """
    import shutil
    
    print(f"{'=' * 80}")
    print(f"LIMPIANDO PROYECTO EXISTENTE")
    print(f"{'=' * 80}")
    
    print(f"Eliminando: {project_dir}")
    
    try:
        # Contar archivos antes
        import subprocess
        result = subprocess.run(['du', '-sh', str(project_dir)], 
                              capture_output=True, text=True, check=False)
        if result.returncode == 0:
            size = result.stdout.split()[0]
            print(f"Tamaño a eliminar: {size}")
        
        # Eliminar
        shutil.rmtree(project_dir)
        print(f"✓ Proyecto eliminado correctamente")
        
    except Exception as e:
        print(f"✗ Error eliminando proyecto: {e}")
        raise


def download_sentinel2_products(workflow_config):
    """
    Descarga productos Sentinel-2 L2A para el AOI y rango de fechas
    
    Args:
        workflow_config: Configuración del workflow con parámetros de descarga

    Returns:
        bool: True si exitoso o si hay productos disponibles
    """
    logger.info(f"{'=' * 80}")
    logger.info(f"PASO 2b: DESCARGA DE SENTINEL-2 L2A")
    logger.info(f"{'=' * 80}")
    logger.info(f"Para cálculo de MSAVI (humedad del suelo)")
    
    # Verificar si ya hay productos Sentinel-2 descargados
    s2_data_dir = Path("data") / "sentinel2_l2a"
    if s2_data_dir.exists():
        existing_products = list(s2_data_dir.glob("S2*_MSIL2A_*.SAFE"))
        if len(existing_products) > 0:
            logger.info(f"✓ Ya hay {len(existing_products)} productos Sentinel-2 descargados")
            logger.info(f"  Saltando descarga (usar productos existentes)")
            return True
    
    cmd = [
        sys.executable,
        "scripts/download_copernicus.py",
        "--aoi-geojson", workflow_config["aoi_geojson"],
        "--start-date", workflow_config["start_date"],
        "--end-date", workflow_config["end_date"],
        "--collection", "SENTINEL-2",
        "--product-type", workflow_config["product_type"]["sentinel2"],
        "--yes",
        "--min-coverage", workflow_config['min_coverage'],
    ]
    
    logger.info(f"Comando: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            check=False,
            text=True
        )
        
        if result.returncode == 0:
            logger.info(f"✓ Productos Sentinel-2 descargados")
            return True
        else:
            # Verificar si se descargó algo a pesar del error
            if s2_data_dir.exists():
                products = list(s2_data_dir.glob("S2*_MSIL2A_*.SAFE"))
                if len(products) > 0:
                    logger.info(f"✓ {len(products)} productos Sentinel-2 disponibles")
                    return True
            
            logger.warning(f"No se descargaron productos Sentinel-2")
            logger.info(f"  MSAVI no estará disponible (opcional)")
            return True  # No es crítico
            
    except Exception as e:
        logger.error(f"✗ Error en descarga de Sentinel-2: {e}")
        return True  # No es crítico para el workflow


def process_msavi_for_project(project_name, aoi_file, start_date, end_date, log=None):
    """
    Procesa productos Sentinel-2 para generar índice MSAVI
    
    MSAVI (Modified Soil Adjusted Vegetation Index) es usado para
    inversión de humedad del suelo en el modelo PSLDA.
    
    Args:
        project_name: Nombre del proyecto
        aoi_file: Path al archivo GeoJSON
        start_date: Fecha inicio (YYYY-MM-DD)
        end_date: Fecha fin (YYYY-MM-DD)
        log: Logger a usar (opcional, usa el global si no se especifica)
        
    Returns:
        bool: True si se procesó al menos un producto exitosamente
    """
    _logger = log if log is not None else logger
    _logger.info(f"{'=' * 80}")
    _logger.info(f"PASO 5.6: PROCESAMIENTO SENTINEL-2 → MSAVI")
    _logger.info(f"{'=' * 80}")
    
    project_dir = Path("processing") / project_name
    s2_data_dir = Path("data") / "sentinel2_l2a"
    
    # Verificar si hay productos Sentinel-2 descargados
    if not s2_data_dir.exists():
        _logger.warning(f"No existe directorio Sentinel-2: {s2_data_dir}")
        _logger.info(f"  Saltando procesamiento MSAVI...")
        return False
    
    s2_products = list(s2_data_dir.glob("S2*_MSIL2A_*.SAFE"))
    
    if not s2_products:
        _logger.warning(f"No hay productos Sentinel-2 descargados")
        _logger.info(f"  Saltando procesamiento MSAVI...")
        return False
    
    _logger.info(f"Productos Sentinel-2 encontrados: {len(s2_products)}")
    
    # Crear directorio de salida para MSAVI
    msavi_dir = project_dir / "sentinel2_msavi"
    msavi_dir.mkdir(parents=True, exist_ok=True)
    
    _logger.info(f"Directorio de salida: {msavi_dir}")
    
    # Filtrar productos por rango de fechas
    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
    
    filtered_products = []
    for product in s2_products:
        # Extraer fecha del nombre: S2A_MSIL2A_20240315T105311_...
        try:
            date_str = product.name.split('_')[2][:8]  # 20240315
            product_date = datetime.strptime(date_str, '%Y%m%d')
            
            if start_dt <= product_date <= end_dt:
                filtered_products.append((product, date_str))
        except Exception as e:
            _logger.warning(f"  Error parseando fecha de {product.name}: {e}")
            continue
    
    if not filtered_products:
        _logger.warning(f"No hay productos Sentinel-2 en el rango de fechas")
        _logger.info(f"  Periodo: {start_date} a {end_date}")
        return False
    
    _logger.info(f"Productos en rango de fechas: {len(filtered_products)}")
    for product, date_str in filtered_products:
        _logger.info(f"  - {date_str}: {product.name}")
    _logger.info("")
    
    # Procesar cada producto
    success_count = 0
    for product, date_str in filtered_products:
        output_file = msavi_dir / f"MSAVI_{date_str}.tif"
        
        # Saltar si ya existe
        if output_file.exists():
            _logger.info(f"✓ {date_str}: MSAVI ya calculado")
            success_count += 1
            continue
        
        _logger.info(f"Procesando {date_str}...")
        
        try:
            # Ejecutar process_sentinel2_msavi.py
            cmd = [
                sys.executable,
                "scripts/process_sentinel2_msavi.py",
                "--s2-product", str(product),
                "--output", str(output_file),
                "--aoi-geojson", str(aoi_file)
            ]
            
            result = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                success_count += 1
                _logger.info(f"  ✓ MSAVI calculado: {output_file.name}")
            else:
                _logger.warning(f"  Error procesando {date_str}")
                if result.stderr:
                    _logger.debug(f"  Error: {result.stderr[:200]}")
                    
        except Exception as e:
            _logger.warning(f"  Excepción procesando {date_str}: {e}")
    
    # Resumen
    _logger.info(f"{'=' * 60}")
    _logger.info(f"RESUMEN MSAVI:")
    _logger.info(f"  Productos procesados: {success_count}/{len(filtered_products)}")
    
    if success_count > 0:
        _logger.info(f"✓ MSAVI calculado para {success_count} fechas")
        _logger.info(f"Visualizar:")
        _logger.info(f"  qgis {msavi_dir}/*.tif")
        _logger.info(f"Uso en PSLDA:")
        _logger.info(f"  MSAVI se usa como feature para inversión de humedad del suelo")
        return True
    else:
        _logger.warning(f"No se pudo calcular MSAVI")
        return False


def calculate_closure_phase_for_project(project_name, log=None):
    """
    Calcula Closure Phase para todas las series InSAR del proyecto
    
    Busca tripletes de interferogramas consecutivos (1→2, 2→3, 1→3)
    y calcula el closure phase para detectar cambios de humedad.
    
    Args:
        project_name: Nombre del proyecto
        log: Logger a usar (opcional, usa el global si no se especifica)
        
    Returns:
        bool: True si se calculó al menos un closure phase exitosamente
    """
    _logger = log if log is not None else logger
    _logger.info(f"{'=' * 80}")
    _logger.info(f"PASO 5.5: CÁLCULO DE CLOSURE PHASE")
    _logger.info(f"{'=' * 80}")
    
    project_dir = Path("processing") / project_name
    
    # Buscar todas las series procesadas
    series_dirs = sorted(project_dir.glob("insar_*_iw*"))
    
    if not series_dirs:
        _logger.warning(f"No se encontraron series InSAR procesadas")
        return False
    
    _logger.info(f"Series encontradas: {len(series_dirs)}")
    for series_dir in series_dirs:
        _logger.info(f"  - {series_dir.name}")
    _logger.info("")
    
    total_triplets = 0
    success_count = 0
    
    for series_dir in series_dirs:
        series_name = series_dir.name
        _logger.info(f"Procesando serie: {series_name}")
        
        # Buscar interferogramas en fusion/insar/
        insar_dir = series_dir / "fusion" / "insar"
        
        if not insar_dir.exists():
            _logger.warning(f"  Directorio InSAR no existe: {insar_dir}")
            continue
        
        # Buscar archivos .dim de interferogramas
        ifg_files = sorted(insar_dir.glob("Ifg_*.dim"))
        
        if len(ifg_files) < 3:
            _logger.warning(f"  Insuficientes interferogramas ({len(ifg_files)}) para closure phase (se necesitan ≥3)")
            continue
        
        _logger.info(f"  Interferogramas encontrados: {len(ifg_files)}")
        
        # Extraer fechas de los interferogramas
        ifg_info = []
        for ifg_file in ifg_files:
            # Nombre formato: Ifg_20220101_20220113.dim
            import re
            match = re.search(r'Ifg_(\d{8})_(\d{8})', ifg_file.name)
            if match:
                date1, date2 = match.groups()
                ifg_info.append({
                    'file': ifg_file,
                    'date1': date1,
                    'date2': date2,
                    'baseline_days': (
                        datetime.strptime(date2, '%Y%m%d') - 
                        datetime.strptime(date1, '%Y%m%d')
                    ).days
                })
        
        if len(ifg_info) < 3:
            _logger.warning(f"  No se pudieron extraer fechas de los interferogramas")
            continue
        
        # Ordenar por fecha de inicio
        ifg_info.sort(key=lambda x: x['date1'])
        
        # Buscar tripletes válidos: necesitamos Ifg(1→2), Ifg(2→3), Ifg(1→3)
        # donde 1, 2, 3 son fechas consecutivas en orden temporal
        triplets = []
        
        for i in range(len(ifg_info)):
            for j in range(i + 1, len(ifg_info)):
                for k in range(j + 1, len(ifg_info)):
                    ifg1 = ifg_info[i]
                    ifg2 = ifg_info[j]
                    ifg3 = ifg_info[k]
                    
                    # Verificar si forman un triplete válido
                    # Triplete: A→B (corto), B→C (corto), A→C (largo)
                    if (ifg1['date2'] == ifg2['date1'] and 
                        ifg1['date1'] == ifg3['date1'] and 
                        ifg2['date2'] == ifg3['date2']):
                        
                        triplets.append({
                            'ifg_12': ifg1['file'],
                            'ifg_23': ifg2['file'],
                            'ifg_13': ifg3['file'],
                            'dates': (ifg1['date1'], ifg1['date2'], ifg2['date2'])
                        })
        
        if not triplets:
            _logger.warning(f"  No se encontraron tripletes válidos (necesario: pares consecutivos + par largo)")
            _logger.info(f"  Estructura esperada: Ifg(A→B) + Ifg(B→C) + Ifg(A→C)")
            continue
        
        _logger.info(f"  Tripletes válidos encontrados: {len(triplets)}")
        
        # Crear directorio de salida para closure phase
        closure_dir = series_dir / "fusion" / "closure_phase"
        closure_dir.mkdir(parents=True, exist_ok=True)
        
        # Calcular closure phase para cada triplete
        for idx, triplet in enumerate(triplets, 1):
            total_triplets += 1
            
            _logger.info(f"  Triplete {idx}/{len(triplets)}:")
            _logger.info(f"    {triplet['dates'][0]} → {triplet['dates'][1]} → {triplet['dates'][2]}")
            
            try:
                # Ejecutar calculate_closure_phase.py
                cmd = [
                    sys.executable,
                    "scripts/calculate_closure_phase.py",
                    str(triplet['ifg_12']),
                    str(triplet['ifg_23']),
                    str(triplet['ifg_13']),
                    "--output", str(closure_dir)
                ]
                
                result = subprocess.run(
                    cmd,
                    check=False,
                    capture_output=True,
                    text=True
                )
                
                if result.returncode == 0:
                    success_count += 1
                    _logger.info(f"    ✓ Closure phase calculado")
                else:
                    _logger.warning(f"    Error calculando closure phase")
                    if result.stderr:
                        _logger.debug(f"    Error: {result.stderr[:200]}")
                        
            except Exception as e:
                _logger.warning(f"    Excepción: {e}")
    
    # Resumen
    _logger.info(f"{'=' * 60}")
    _logger.info(f"RESUMEN CLOSURE PHASE:")
    _logger.info(f"  Tripletes procesados: {total_triplets}")
    _logger.info(f"  Exitosos: {success_count}")
    
    if success_count > 0:
        _logger.info(f"✓ Closure phase calculado para {success_count} tripletes")
        _logger.info(f"Visualizar:")
        _logger.info(f"  qgis {project_dir}/insar_*/fusion/closure_phase/*_abs.tif")
        return True
    else:
        _logger.warning(f"No se pudo calcular closure phase")
        return False


def main():
    global logger

    print(f"Iniciando workflow completo")
    # Modo interactivo o parámetros
    aoi_file = select_aoi_interactive()
    if not aoi_file:
        return 1
    
    project_name = aoi_file.stem
    project_dir = Path("processing") / project_name

    # Eliminar proyecto existente si se solicitó
    if check_project_exists(project_name):
        response = input(f"️Proyecto existente encontrado. ¿Eliminar y empezar de cero? (y/N): ").strip().lower()
        if response == 'y':
            clean_project_directory(project_dir)
        else:
            print(f"Continuando con proyecto existente")

    # Crear directorio si no existe
    project_dir.mkdir(parents=True, exist_ok=True)

    # Configurar logger una vez conocido el proyecto
    logger = LoggerConfig.setup_aoi_logger(
        aoi_project_dir=str(project_dir),
        log_name="workflow_complete"
    )

    start_date, end_date = select_date_range_interactive()
    if not start_date:
        return 1

    # Crear configuración inicial del workflow
    workflow_config = {
        'project_name': project_name,
        'start_date': start_date,
        'end_date': end_date,
        'download': True, 
        'satellites': ['S1A', 'S1C'],
        'orbit_type': 'POEORB',
        'orbit_direction': ['DESCENDING', 'ASCENDING'],
        'products_to_download': {'sentinel_1':'SLC', 'sentinel_2':'L2A'},
        'aoi_file': str(aoi_file),
        'collection': ['SENTINEL-1', 'SENTINEL-2'],
        'min_coverage': 100.0,
    }

    # Mostrar configuración
    logger.info(f"{'=' * 80}")
    logger.info(f"CONFIGURACIÓN DE SATÉLITES Y ÓRBITAS")
    logger.info(f"{'=' * 80}")
    logger.info(f"Satélites: {workflow_config['satellites']} ")
    logger.info(f"Tipo de órbita: {workflow_config['orbit_type']} (Precisas ±5cm)")
    logger.info(f"Dirección: {workflow_config['orbit_direction']}")
    logger.info(f"Productos a descargar: {workflow_config['products_to_download']}")
    logger.info(f"{'=' * 80}")

    # PASO 1: Descargar órbitas
    if download_orbits(workflow_config):
        workflow_config['download_orbits'] = True
    else:
        logger.error(f"Error en descarga de órbitas")
        workflow_config['download_orbits'] = False

    # PASO 2: Descargar productos SLC
    if download_products(workflow_config):
        workflow_config['download_products'] = True
    else:
        logger.error(f"Error en descarga de productos SLC")
        workflow_config['download_products'] = False

    # PASO 2b: Descargar productos Sentinel-2 para MSAVI
    if download_sentinel2_products(workflow_config):
        workflow_config['download_sentinel2'] = True
    else:
        logger.error(f"Error en descarga de productos Sentinel-2")
        workflow_config['download_sentinel2'] = False

    # PASO 3: Crear proyecto AOI
    if create_aoi_project(aoi_file, project_name):
        workflow_config['create_aoi_project'] = True
    else:
        workflow_config['create_aoi_project'] = False

    # PASO 3 & 4: Para cada órbita, generar configuraciones y procesar series
    overall_success = True
    orbit_results = {}  # Guardar resultados de cada órbita procesada

    for orbit_direction in workflow_config['orbit_direction']:
        logger.info(f"{'#' * 80}")
        logger.info(f"# ÓRBITA: {orbit_direction}")
        logger.info(f"{'#' * 80}")

        # PASO 3: Generar configuraciones para esta órbita
        if not generate_product_configurations(workflow_config, orbit_direction):
            logger.error(f"✗ Error generando configuraciones {orbit_direction}")
            orbit_results[orbit_direction] = None
            overall_success = False
            continue  # Continuar con la siguiente órbita

        # PASO 4: Procesar cada serie COMPLETA (InSAR + Stats)
        # Repositorio siempre activo: usa productos existentes y guarda nuevos
        orbit_success = run_processing(project_name, orbit_direction=orbit_direction,
                                       use_repository=True,
                                       save_to_repository=True)
        
        # Evaluar calidad del procesamiento
        quality = evaluate_orbit_processing_quality(project_name, orbit_direction, log=logger)
        orbit_results[orbit_direction] = quality
        
        logger.info(f"{'=' * 80}")
        logger.info(f"EVALUACIÓN {orbit_direction}:")
        logger.info(f"  Éxito: {'✓' if quality['success'] else '✗'}")
        logger.info(f"  Completo: {'✓' if quality['complete'] else '✗'}")
        logger.info(f"  Detalles: {quality['details']}")
        logger.info(f"{'=' * 80}")
        
        if not orbit_success:
            overall_success = False

    # PASO 5: Recorte a Suelo Urbano (solo si procesamiento exitoso)
    if overall_success:
        logger.info(f"{'=' * 80}")
        logger.info(f"PASO 5: RECORTE A SUELO URBANO")
        logger.info(f"{'=' * 80}")
        
        mcc_file = Path("data/cobertes-sol-v1r0-2023.gpkg")
        
        if mcc_file.exists():
            logger.info(f"✓ MCC encontrado: {mcc_file}")
            logger.info(f"Extrayendo áreas urbanas y recortando productos...")
            
            try:
                # Ejecutar workflow de crop urbano
                crop_cmd = [
                    "bash", "scripts/workflow_urban_crop.sh",
                    str(project_dir),
                    str(mcc_file)
                ]
                
                result = subprocess.run(
                    crop_cmd,
                    cwd=Path.cwd(),
                    capture_output=True,
                    text=True
                )
                
                if result.returncode == 0:
                    logger.info(f"✓ Recorte urbano completado")
                    
                    # Contar productos generados
                    urban_dir = project_dir / "urban_products"
                    if urban_dir.exists():
                        n_products = len(list(urban_dir.rglob("*.tif")))
                        logger.info(f"  Productos urbanos generados: {n_products}")
                else:
                    logger.warning(f"Recorte urbano con advertencias")
                    if result.stderr:
                        logger.debug(f"  Error: {result.stderr[:200]}")
                        
            except Exception as e:
                logger.warning(f"Error en recorte urbano (no crítico): {e}")
        else:
            logger.warning(f"MCC no encontrado en {mcc_file}")
            logger.info(f"  Descárgalo desde: https://www.icgc.cat/ca/Descarregues/Cobertes-del-sol")
            logger.info(f"  Saltando recorte urbano...")
    
    # PASO 5.5: Calcular Closure Phase (ANTES de la limpieza que elimina .dim)
    if overall_success:
        try:
            calculate_closure_phase_for_project(project_name, log=logger)
        except Exception as e:
            logger.warning(f"Error calculando closure phase (no crítico): {e}")
    
    # PASO 5.6: Procesar Sentinel-2 para calcular MSAVI (ANTES de la limpieza)
    if overall_success:
        try:
            process_msavi_for_project(project_name, aoi_file, start_date, end_date, log=logger)
        except Exception as e:
            logger.warning(f"Error calculando MSAVI (no crítico): {e}")
    
    # PASO 6: Limpieza de archivos intermedios (SIEMPRE se ejecuta)
    logger.info(f"{'=' * 80}")
    logger.info(f"PASO 6: LIMPIEZA DE WORKSPACE")
    logger.info(f"{'=' * 80}")

    try:
        cleanup_cmd = [
            "python3", "scripts/cleanup_after_urban_crop.py",
            str(project_dir)
        ]
        
        result = subprocess.run(
            cleanup_cmd,
            cwd=Path.cwd(),
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            logger.info(f"✓ Limpieza completada exitosamente")
            
            # Extraer espacio liberado del output
            if "liberado:" in result.stdout:
                for line in result.stdout.split(''):
                    if "liberado:" in line:
                        logger.info(f"  {line.strip()}")
                        break
        else:
            logger.warning(f"Limpieza completada con advertencias")

    except Exception as e:
        logger.warning(f"Error durante limpieza (no crítico): {e}")
        # La limpieza no debe afectar el resultado general del workflow

    # Resumen final
    print_summary(project_name, overall_success, orbit_results=orbit_results)
    
    if overall_success:
        logger.info("="*80)
        logger.info("WORKFLOW COMPLETADO EXITOSAMENTE")
        logger.info("="*80)
    else:
        logger.error("="*80)
        logger.error("WORKFLOW COMPLETADO CON ERRORES")
        logger.error("="*80)

    return 0 if overall_success else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.warning(f"Workflow interrumpido por el usuario")
        sys.exit(130)
    except Exception as e:
        logger.error(f"ERROR: {str(e)}", exc_info=True)
        sys.exit(1)
