#!/usr/bin/env python3
"""
Descargador de Órbitas Sentinel-1 - Proyecto SAR Water Leak Detection

Este script descarga archivos de órbita precisos (POEORB) para satélites Sentinel-1
del mismo periodo de datos descargados. Las órbitas son necesarias para procesamiento
InSAR de alta precisión.

Basado en: https://github.com/AlexeyPechnikov/S1orbits

Autor: SAR-based water leak detection project
Versión: 1.0
Fecha: 2025-11-15
"""

import argparse
import logging
import os
import re
import sys
import zipfile
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import requests
import xmltodict
# importar las credenciales desde un archivo .env externo si existe
from dotenv import load_dotenv

load_dotenv()

# importar utilidades comunes
from common_utils import get_snap_orbits_dir
from logging_utils import LoggerConfig

# Logger global - se configurará en main()
logger = None


# ============================================================================
# CONFIGURACIÓN
# ============================================================================

# Use absolute path for BASE_DIR to work correctly from any directory
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
BASE_DIR = os.path.join(parent_dir, "data")
ORBITS_DIR = os.path.join(BASE_DIR, "orbits")

# URL base para descarga de órbitas ESA
ESA_ORBITS_URL = 'https://step.esa.int/auxdata/orbits/Sentinel-1/{product}/{satellite}/{year}/{month:02}'

HTTP_TIMEOUT = 30

# Offset para determinar cobertura temporal de órbitas RESORB
OFFSET_START = timedelta(hours=1)
OFFSET_END = timedelta(hours=1)


def download_orbit_file(
    satellite: str,
    product: str,
    orbit_filename: str,
    year: str,
    month: str,
    base_url: str
) -> Tuple[bool, Optional[str], List[str]]:
    """
    Descarga un archivo de órbita individual directamente al directorio de SNAP

    Returns:
        Tuple de (éxito, mensaje_error, lista_fechas_cubiertas)
    """
    # Determinar directorio de SNAP usando función común
    snap_base = get_snap_orbits_dir()

    # Parsear nombre de órbita para determinar cobertura temporal
    # Formato: S1A_OPER_AUX_POEORB_OPOD_20240222T070809_V20240201T225942_20240203T005942.EOF.zip
    try:
        parts = orbit_filename[:-8].split('_')  # Remover .EOF.zip
        start_time_str = parts[6][1:]  # Remover 'V' inicial
        stop_time_str = parts[7]

        start_time_dt = datetime.strptime(start_time_str, '%Y%m%dT%H%M%S')
        stop_time_dt = datetime.strptime(stop_time_str, '%Y%m%dT%H%M%S')
        interval = stop_time_dt - start_time_dt

        # Determinar fechas cubiertas por esta órbita
        if product == 'POEORB':
            # POEORB cubre un día completo
            assert interval.days == 1, f"POEORB debería cubrir 1 día, cubre {interval.days}"
            date_dts = [start_time_dt + timedelta(days=1)]
        else:
            return False, f"Producto desconocido: {product}", []

        dates_covered = list(set([date_dt.strftime('%Y-%m-%d') for date_dt in date_dts]))

    except Exception as e:
        return False, f"Error parseando nombre de órbita: {e}", []

    # Estructura de directorios en SNAP: POEORB/satellite/year/month/
    snap_orbit_dir = snap_base / product / satellite / year / f"{int(month):02d}"
    snap_orbit_dir.mkdir(parents=True, exist_ok=True)

    filename_eof = orbit_filename[:-4]  # Remover .zip para obtener .EOF
    snap_orbit_path = snap_orbit_dir / filename_eof

    # Verificar si ya existe en SNAP
    if snap_orbit_path.exists() and snap_orbit_path.stat().st_size > 0:
        return True, None, dates_covered  # Ya existe, no descargar

    # Descargar archivo
    url = f"{base_url}/{orbit_filename}"

    try:
        with requests.get(url, timeout=HTTP_TIMEOUT) as response:
            response.raise_for_status()

            # Verificar que sea un ZIP válido
            if not zipfile.is_zipfile(BytesIO(response.content)):
                return False, f"Archivo descargado no es ZIP válido", dates_covered

            # Extraer y validar contenido
            with zipfile.ZipFile(BytesIO(response.content), 'r') as zip_in:
                zip_files = zip_in.namelist()

                if len(zip_files) == 0:
                    return False, "Archivo ZIP vacío", dates_covered

                if filename_eof not in zip_files:
                    # Buscar archivo EOF en cualquier subdirectorio
                    eof_files = [f for f in zip_files if f.endswith('.EOF')]
                    if not eof_files:
                        return False, f"No se encontró archivo .EOF en ZIP", dates_covered
                    filename_eof = eof_files[0]

                # Extraer contenido y validar XML
                orbit_content = zip_in.read(filename_eof)

                try:
                    doc = xmltodict.parse(orbit_content)
                except Exception as e:
                    return False, f"XML inválido: {e}", dates_covered

                # Guardar directamente en SNAP (archivo .EOF descomprimido)
                temp_path = str(snap_orbit_path) + '.tmp'
                with open(temp_path, 'wb') as f:
                    f.write(orbit_content)

                # Mover archivo temporal al final
                os.rename(temp_path, str(snap_orbit_path))

                logger.info(f"   ✅ {', '.join(dates_covered)}: {orbit_filename} → SNAP")

                # Opcionalmente guardar copia en data/orbits como backup
                backup_dir = Path(ORBITS_DIR) / satellite / year / f"{int(month):02d}"
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup_path = backup_dir / orbit_filename

                if not backup_path.exists():
                    with zipfile.ZipFile(str(backup_path), 'w', zipfile.ZIP_DEFLATED) as zip_out:
                        zip_out.writestr(os.path.basename(filename_eof), orbit_content)

        return True, None, dates_covered

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            # Algunas órbitas pueden no existir, no es error crítico
            return False, None, dates_covered
        return False, f"Error HTTP {e.response.status_code}", dates_covered
    except Exception as e:
        return False, str(e), dates_covered

def download_orbits_for_period(
    satellite: str,
    orbit_type: str,
    start_date: datetime,
    end_date: datetime
) -> Dict:
    """
    Descarga órbitas para un satélite y periodo específico
    """
    logger.info(f"\nDescargando órbitas {orbit_type} para {satellite}")
    logger.info(f"   Periodo: {start_date.date()} → {end_date.date()}")

    # Determinar meses a cubrir
    months_to_download = set()
    current = start_date.replace(day=1)
    end_month = end_date.replace(day=1)

    while current <= end_month:
        months_to_download.add((current.year, current.month))
        # Avanzar al siguiente mes
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)

    logger.info(f"   Meses a revisar: {len(months_to_download)}")

    total_downloaded = 0
    total_errors = 0

    for year, month in sorted(months_to_download):
        url = ESA_ORBITS_URL.format(
            product=orbit_type,
            satellite=satellite,
            year=year,
            month=month
        )

        logger.info(f"\n   📅 {year}-{month:02d}")

        try:
            # Obtener lista de archivos disponibles
            with requests.get(url, timeout=HTTP_TIMEOUT) as response:
                response.raise_for_status()
                lines = response.text.splitlines()

                # Parsear HTML para extraer archivos de órbita
                pattern = r'<a href="(S1\w_OPER_AUX_(POE|RES)ORB_OPOD_\d{8}T\d{6}_V\d{8}T\d{6}_\d{8}T\d{6}.EOF.zip)">'

                orbit_files = []
                for line in lines:
                    match = re.search(pattern, line)
                    if match:
                        orbit_files.append(match.group(1))

                logger.info(f"      Archivos disponibles: {len(orbit_files)}")

                # Descargar cada archivo de órbita
                for orbit_file in orbit_files:
                    success, error, dates = download_orbit_file(
                        satellite=satellite,
                        product=orbit_type,
                        orbit_filename=orbit_file,
                        year=str(year),
                        month=str(month),
                        base_url=url
                    )

                    if success:
                        total_downloaded += 1
                    elif error:
                        total_errors += 1
                        logger.info(f"      Error en {orbit_file}: {error}")

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.info(f"      No hay órbitas disponibles para este mes")
            else:
                logger.info(f"      Error HTTP {e.response.status_code}: {e}")
                total_errors += 1
        except Exception as e:
            logger.info(f"      Error: {e}")
            total_errors += 1

    return {
        'satellite': satellite,
        'product': orbit_type,
        'downloaded': total_downloaded,
        'errors': total_errors
    }


def main():
    parser = argparse.ArgumentParser(
        description='Descarga de órbitas Sentinel-1 para procesamiento InSAR',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:

  # Descargar órbitas POEORB para S1A en periodo específico
  python3 download_orbits.py --satellite S1A --product POEORB --start-date 2025-01-01 --end-date 2025-06-01

  # Descargar para múltiples satélites
  python3 download_orbits.py --satellite S1A S1B --product POEORB --start-date 2025-01-01
        """
    )

    parser.add_argument('--satellite', nargs='+',
                       choices=['S1A', 'S1B', 'S1C'],
                       help='Satélite(s) a descargar')
    parser.add_argument('--orbit_type', choices=['POEORB'],
                       default='POEORB',
                       help='Tipo de órbita (default: POEORB)')
    parser.add_argument('--start-date', help='Fecha inicio (YYYY-MM-DD)')
    parser.add_argument('--end-date', help='Fecha fin (YYYY-MM-DD)')
    parser.add_argument('--log-dir', default='logs',
                       help='Directorio donde guardar logs (default: logs/)')

    args = parser.parse_args()

    # Configurar logger con directorio especificado
    global logger
    logger = LoggerConfig.setup_aoi_logger(
        aoi_project_dir=args.log_dir,
        log_name='download_orbits',
        level=logging.INFO,
        console_level=logging.INFO
    )

    # Banner
    logger.info("="*80)
    logger.info("DESCARGADOR DE ÓRBITAS SENTINEL-1")
    logger.info("="*80)

    satellites = args.satellite
    start_date = datetime.strptime(args.start_date, '%Y-%m-%d')
    end_date = datetime.strptime(args.end_date, '%Y-%m-%d')

    # Descargar órbitas
    results = []
    for satellite in satellites:
        result = download_orbits_for_period(
            satellite=satellite,
            orbit_type=args.orbit_type,
            start_date=start_date,
            end_date=end_date
        )
        results.append(result)

    # Resumen
    logger.info("\n" + "="*80)
    logger.info("RESUMEN")
    logger.info("="*80)

    total_downloaded = sum(r['downloaded'] for r in results)
    total_errors = sum(r['errors'] for r in results)

    for result in results:
        logger.info(f"{result['satellite']}: {result['downloaded']} descargados, {result['errors']} errores")

    # Determinar directorio de SNAP usando función común
    snap_dir = get_snap_orbits_dir()
    logger.info(f"Snap dir: {snap_dir}")

    logger.info(f"\n✅ Total: {total_downloaded} órbitas descargadas")
    logger.info(f"📁 Guardadas en: {snap_dir}")
    logger.info(f"📦 Backup en: {ORBITS_DIR}")

    # Workflow completion message
    if total_errors == 0:
        logger.info(f"\n✅ Descarga completada exitosamente")

    return 0 if total_errors == 0 else 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.info("\n\n️  Interrumpido")
        sys.exit(130)
    except Exception as e:
        logger.exception(f"\n\n❌ Error inesperado: {e}")
        sys.exit(1)
