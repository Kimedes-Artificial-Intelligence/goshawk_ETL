#!/usr/bin/env python3
"""
Smart Workflow Orchestrator - Workflow optimizado con consulta a BD.

Este script implementa un workflow inteligente que:
1. Consulta la BD para determinar qué productos ya están procesados
2. Decide la estrategia óptima:
   - CROP_ONLY: Si todo está procesado → Solo crop (5-15 min)
   - PROCESS_ONLY: Si SLCs descargados pero no procesados → Procesar (2-3 horas)
   - FULL_WORKFLOW: Si faltan productos → Workflow completo (6-8 horas)
3. Ejecuta solo las etapas necesarias

Author: goshawk_ETL + satelit_metadata integration
Version: 2.0
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Add scripts to path
script_dir = Path(__file__).parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

from smart_workflow_planner import SmartWorkflowPlanner
from db_integration import get_db_integration
from logging_utils import LoggerConfig


logger = logging.getLogger(__name__)


def run_command(cmd, description, dry_run=False):
    """
    Ejecuta un comando shell y maneja errores.

    Args:
        cmd: Comando a ejecutar (string o lista)
        description: Descripción para logging
        dry_run: Si True, solo imprime el comando sin ejecutarlo

    Returns:
        True si exitoso, False si falla
    """
    logger.info(f"\n{'=' * 80}")
    logger.info(f"EJECUTANDO: {description}")
    logger.info(f"{'=' * 80}")

    if isinstance(cmd, list):
        cmd_str = ' '.join(cmd)
    else:
        cmd_str = cmd

    logger.info(f"Comando: {cmd_str}")

    if dry_run:
        logger.info("  [DRY RUN] - No se ejecuta realmente")
        return True

    try:
        result = subprocess.run(
            cmd if isinstance(cmd, list) else cmd.split(),
            check=True,
            capture_output=True,
            text=True
        )

        if result.stdout:
            logger.info(result.stdout)

        logger.info(f"✓ {description} completado exitosamente")
        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"✗ Error en {description}")
        logger.error(f"Código de salida: {e.returncode}")
        if e.stdout:
            logger.error(f"STDOUT:\n{e.stdout}")
        if e.stderr:
            logger.error(f"STDERR:\n{e.stderr}")
        return False
    except Exception as e:
        logger.error(f"✗ Excepción al ejecutar {description}: {e}")
        return False


def crop_to_aoi_workflow(aoi_geojson, track_id, processing_base_dir, dry_run=False):
    """
    Ejecuta solo el paso de crop a AOI (FAST PATH).

    Args:
        aoi_geojson: Path al archivo GeoJSON del AOI
        track_id: ID del track (ej: desc_iw1_t088)
        processing_base_dir: Directorio base de procesamiento
        dry_run: Si True, no ejecuta realmente

    Returns:
        True si exitoso
    """
    logger.info(f"\n{'=' * 80}")
    logger.info(f"🚀 FAST PATH: CROP ONLY para track {track_id}")
    logger.info(f"{'=' * 80}")
    logger.info("Todos los productos ya están procesados")
    logger.info("Saltando descarga y procesamiento → Crop directo a AOI")

    # Parsear track_id
    parts = track_id.split('_')
    orbit_short = parts[0]  # desc o asce
    subswath = parts[1].upper()  # IW1, IW2

    orbit_direction = "DESCENDING" if orbit_short == "desc" else "ASCENDING"

    # Directorio de productos procesados del track
    insar_dir = processing_base_dir / f"insar_{orbit_short}_{subswath.lower()}"
    polar_dir = processing_base_dir / f"polarimetry_{orbit_short}_{subswath.lower()}"

    success = True

    # 1. Crop InSAR productos
    if insar_dir.exists():
        cmd = [
            "python", str(script_dir / "crop_insar_to_aoi.py"),
            "--insar-dir", str(insar_dir),
            "--aoi-geojson", str(aoi_geojson),
            "--output-dir", str(insar_dir / "aoi_crop")
        ]
        success = run_command(cmd, f"Crop InSAR ({track_id}) a AOI", dry_run) and success
    else:
        logger.warning(f"⚠️  Directorio InSAR no encontrado: {insar_dir}")

    # 2. Crop Polarimetría
    if polar_dir.exists():
        cmd = [
            "python", str(script_dir / "crop_polarimetry_to_aoi.py"),
            "--polar-dir", str(polar_dir),
            "--aoi-geojson", str(aoi_geojson),
            "--output-dir", str(polar_dir / "aoi_crop")
        ]
        success = run_command(cmd, f"Crop Polarimetría ({track_id}) a AOI", dry_run) and success
    else:
        logger.warning(f"⚠️  Directorio Polarimetría no encontrado: {polar_dir}")

    return success


def processing_only_workflow(aoi_geojson, track_id, orbit_direction, subswath,
                              selected_products_file, processing_base_dir,
                              repo_dir, dry_run=False):
    """
    Ejecuta solo procesamiento (SLCs ya descargados - MEDIUM PATH).

    Args:
        aoi_geojson: Path al GeoJSON del AOI
        track_id: ID del track
        orbit_direction: ASCENDING o DESCENDING
        subswath: IW1, IW2, IW3
        selected_products_file: Archivo con productos seleccionados
        processing_base_dir: Directorio base de procesamiento
        repo_dir: Directorio del repositorio
        dry_run: Si True, no ejecuta realmente

    Returns:
        True si exitoso
    """
    logger.info(f"\n{'=' * 80}")
    logger.info(f"⚡ MEDIUM PATH: PROCESS ONLY para track {track_id}")
    logger.info(f"{'=' * 80}")
    logger.info("SLCs ya descargados, procesando InSAR + Polarimetría...")

    orbit_short = orbit_direction.lower()[:4]

    success = True

    # 1. Procesar InSAR
    cmd = [
        "python", str(script_dir / "process_insar_series.py"),
        "--config", str(selected_products_file),
        "--output-dir", str(processing_base_dir),
        "--orbit", orbit_direction,
        "--subswath", subswath
    ]
    success = run_command(cmd, f"Procesar InSAR ({track_id})", dry_run) and success

    # 2. Procesar Polarimetría
    cmd = [
        "python", str(script_dir / "process_polarimetry.py"),
        "--config", str(selected_products_file),
        "--output-dir", str(processing_base_dir),
        "--orbit", orbit_direction,
        "--subswath", subswath
    ]
    success = run_command(cmd, f"Procesar Polarimetría ({track_id})", dry_run) and success

    # 3. Añadir productos al repositorio
    insar_dir = processing_base_dir / f"insar_{orbit_short}_{subswath.lower()}"
    if insar_dir.exists() and not dry_run:
        cmd = [
            "python", str(script_dir / "insar_repository.py"),
            "--add-products", str(insar_dir),
            "--orbit", orbit_direction,
            "--subswath", subswath
        ]
        # Extraer track number del track_id
        track_num = int(track_id.split('_t')[-1])
        cmd.extend(["--track", str(track_num)])

        success = run_command(cmd, f"Añadir productos InSAR a repositorio", dry_run) and success

    # 4. Crop a AOI
    success = crop_to_aoi_workflow(aoi_geojson, track_id, processing_base_dir, dry_run) and success

    return success


def full_workflow(aoi_geojson, start_date, end_date, orbit_direction, subswath,
                  slc_dir, processing_base_dir, repo_dir, dry_run=False):
    """
    Ejecuta workflow completo (DESCARGA + PROCESO + CROP).

    Args:
        aoi_geojson: Path al GeoJSON del AOI
        start_date: Fecha inicio
        end_date: Fecha fin
        orbit_direction: ASCENDING o DESCENDING
        subswath: IW1, IW2, IW3
        slc_dir: Directorio de SLCs
        processing_base_dir: Directorio base de procesamiento
        repo_dir: Directorio del repositorio
        dry_run: Si True, no ejecuta realmente

    Returns:
        True si exitoso
    """
    logger.info(f"\n{'=' * 80}")
    logger.info(f"🔄 FULL WORKFLOW para {orbit_direction} {subswath}")
    logger.info(f"{'=' * 80}")
    logger.info("Faltan productos → Ejecutando workflow completo")

    success = True

    # 1. Descargar productos (si faltan)
    cmd = [
        "python", str(script_dir / "download_copernicus.py"),
        "--collection", "SENTINEL-1",
        "--aoi-geojson", str(aoi_geojson),
        "--start-date", start_date.strftime("%Y-%m-%d"),
        "--end-date", end_date.strftime("%Y-%m-%d"),
        "--orbit-direction", orbit_direction,
        "--output-dir", str(slc_dir)
    ]
    success = run_command(cmd, f"Descargar SLCs ({orbit_direction})", dry_run) and success

    # 2. Seleccionar productos para la serie
    orbit_short = orbit_direction.lower()[:4]
    config_file = processing_base_dir / f"selected_products_{orbit_short}_{subswath.lower()}.json"

    cmd = [
        "python", str(script_dir / "select_multiswath_series.py"),
        "--data-dir", str(slc_dir),
        "--aoi-geojson", str(aoi_geojson),
        "--output-dir", str(processing_base_dir),
        "--orbit-direction", orbit_direction,
        "--start-date", start_date.strftime("%Y-%m-%d"),
        "--end-date", end_date.strftime("%Y-%m-%d")
    ]
    success = run_command(cmd, f"Seleccionar serie ({orbit_direction} {subswath})", dry_run) and success

    # 3. Procesar (reutilizar processing_only_workflow)
    if config_file.exists():
        track_id = f"{orbit_short}_{subswath.lower()}_t000"  # Track número pendiente
        success = processing_only_workflow(
            aoi_geojson, track_id, orbit_direction, subswath,
            config_file, processing_base_dir, repo_dir, dry_run
        ) and success
    else:
        logger.error(f"✗ Archivo de configuración no encontrado: {config_file}")
        success = False

    return success


def main():
    parser = argparse.ArgumentParser(
        description="Smart Workflow Orchestrator - Workflow optimizado con BD"
    )

    # Parámetros de AOI y temporalidad
    parser.add_argument("--aoi-geojson", required=True, help="Path al archivo GeoJSON del AOI")
    parser.add_argument("--start-date", required=True, help="Fecha de inicio (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="Fecha de fin (YYYY-MM-DD)")

    # Parámetros de órbita
    parser.add_argument("--orbit", choices=["ASCENDING", "DESCENDING"],
                        default="DESCENDING", help="Dirección de órbita")
    parser.add_argument("--subswaths", nargs="+", default=["IW1", "IW2"],
                        help="Subswaths a procesar")

    # Directorios
    parser.add_argument("--slc-dir", default="data/sentinel1_slc",
                        help="Directorio de SLCs (relativo al repositorio)")
    parser.add_argument("--processing-dir", default="processing",
                        help="Directorio de procesamiento (relativo al repositorio)")
    parser.add_argument("--repo-dir", default="data/processed_products",
                        help="Directorio del repositorio (relativo al repositorio)")

    # Opciones
    parser.add_argument("--dry-run", action="store_true",
                        help="Mostrar plan sin ejecutar")
    parser.add_argument("--force-full", action="store_true",
                        help="Forzar workflow completo (ignorar BD)")
    parser.add_argument("--log-dir", default="logs",
                        help="Directorio de logs")

    args = parser.parse_args()

    # Configurar logger
    global logger
    logger = LoggerConfig.setup_script_logger(
        script_name="smart_workflow",
        log_dir=args.log_dir,
        level=logging.INFO
    )

    # Parsear fechas
    start_date = datetime.strptime(args.start_date, "%Y-%m-%d")
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d")

    # Paths - Convertir a absolutas desde el directorio del repositorio
    repo_root = Path(__file__).parent.parent  # goshawk_ETL root
    aoi_geojson = Path(args.aoi_geojson)
    if not aoi_geojson.is_absolute():
        aoi_geojson = repo_root / aoi_geojson

    slc_dir = Path(args.slc_dir)
    if not slc_dir.is_absolute():
        slc_dir = repo_root / slc_dir

    processing_base_dir = Path(args.processing_dir)
    if not processing_base_dir.is_absolute():
        processing_base_dir = repo_root / processing_base_dir

    repo_dir = Path(args.repo_dir)
    if not repo_dir.is_absolute():
        repo_dir = repo_root / repo_dir

    # Resolver symlinks si existen
    if slc_dir.is_symlink():
        slc_dir = slc_dir.resolve()
    if repo_dir.is_symlink():
        repo_dir = repo_dir.resolve()

    # Header
    logger.info("\n" + "=" * 80)
    logger.info("🧠 SMART WORKFLOW ORCHESTRATOR")
    logger.info("=" * 80)
    logger.info(f"AOI: {aoi_geojson}")
    logger.info(f"Período: {args.start_date} → {args.end_date}")
    logger.info(f"Órbita: {args.orbit}")
    logger.info(f"Subswaths: {', '.join(args.subswaths)}")
    logger.info(f"SLC Directory: {slc_dir}")
    logger.info(f"Processing Directory: {processing_base_dir}")

    if args.dry_run:
        logger.info("\n⚠️  DRY RUN MODE - No se ejecutarán comandos reales")

    if args.force_full:
        logger.info("\n⚠️  FORCE FULL WORKFLOW - Ignorando estado de BD")

    # Inicializar planner
    planner = SmartWorkflowPlanner()

    if not planner.db_available and not args.force_full:
        logger.warning("\n⚠️  Base de datos no disponible")
        logger.warning("    Ejecutando en modo legacy (workflow completo)")
        logger.warning("    Para habilitar optimización: cd ../satelit_metadata && make setup")
        args.force_full = True

    # Planificar workflow
    decisions = planner.plan_workflow(
        aoi_geojson=str(aoi_geojson),
        start_date=start_date,
        end_date=end_date,
        orbit_directions=[args.orbit],
        subswaths=args.subswaths
    )

    # Mostrar plan
    planner.print_workflow_plan(decisions)

    # Si es dry run, terminar aquí
    if args.dry_run:
        logger.info("\n" + "=" * 80)
        logger.info("DRY RUN COMPLETADO - No se ejecutó ningún comando")
        logger.info("=" * 80)
        return 0

    # Pedir confirmación (a menos que sea forzado)
    if not args.force_full and planner.db_available:
        confirm = input("\n¿Proceder con este plan? (y/n): ")
        if confirm.lower() != 'y':
            logger.info("Workflow cancelado por el usuario")
            return 0

    # Ejecutar workflow según decisión
    logger.info("\n" + "=" * 80)
    logger.info("INICIANDO EJECUCIÓN")
    logger.info("=" * 80)

    overall_success = True

    for track_id, decision in decisions.items():
        logger.info(f"\n{'=' * 80}")
        logger.info(f"PROCESANDO TRACK: {track_id}")
        logger.info(f"{'=' * 80}")
        logger.info(f"Decisión: {decision.reason}")

        if args.force_full:
            # Forzar workflow completo
            logger.info("⚠️  FORCE MODE: Ejecutando workflow completo")
            success = full_workflow(
                aoi_geojson, start_date, end_date, decision.orbit_direction,
                decision.subswath, slc_dir, processing_base_dir, repo_dir
            )
        elif decision.needs_crop_only:
            # FAST PATH: Solo crop
            success = crop_to_aoi_workflow(
                aoi_geojson, track_id, processing_base_dir
            )
        elif not decision.needs_download and decision.needs_processing:
            # MEDIUM PATH: Solo procesamiento
            orbit_short = decision.orbit_direction.lower()[:4]
            config_file = processing_base_dir / f"selected_products_{orbit_short}_{decision.subswath.lower()}.json"

            success = processing_only_workflow(
                aoi_geojson, track_id, decision.orbit_direction, decision.subswath,
                config_file, processing_base_dir, repo_dir
            )
        else:
            # FULL WORKFLOW
            success = full_workflow(
                aoi_geojson, start_date, end_date, decision.orbit_direction,
                decision.subswath, slc_dir, processing_base_dir, repo_dir
            )

        if not success:
            logger.error(f"✗ Error procesando track {track_id}")
            overall_success = False
        else:
            logger.info(f"✓ Track {track_id} procesado exitosamente")

    # Resumen final
    logger.info("\n" + "=" * 80)
    logger.info("WORKFLOW COMPLETADO")
    logger.info("=" * 80)

    if overall_success:
        logger.info("✓ Todos los tracks procesados exitosamente")
        return 0
    else:
        logger.error("✗ Algunos tracks fallaron - revisar logs")
        return 1


if __name__ == "__main__":
    sys.exit(main())
