#!/usr/bin/env python3
"""
Script: run_batch_aoi_workflow.py
Descripción: Ejecuta el workflow completo para múltiples AOI con la misma configuración

Este script permite procesar varios AOI en lote con parámetros predefinidos:
- Últimos 3 meses por defecto (configurable)
- Satélite S1C por defecto (configurable)
- Órbita descendente (configurable)
- Órbitas POEORB (precisas) por defecto

Uso:
  # Por nombre de archivo (sin extensión .geojson)
  python run_batch_aoi_workflow.py barcelona madrid valencia

  # Desde archivo de texto con lista de AOI
  python run_batch_aoi_workflow.py --from-file aoi_list.txt

  # Con configuración personalizada
  python run_batch_aoi_workflow.py --months 6 --satellite S1A --orbit ASCENDING barcelona

  # Ver lista de AOI disponibles
  python run_batch_aoi_workflow.py --list
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# Importar módulos del workflow
sys.path.append(os.path.join(os.path.dirname(__file__), 'scripts'))
from scripts.logging_utils import LoggerConfig
from scripts.common_utils import Colors

# Importar funciones del workflow principal
import run_complete_workflow as workflow


def list_available_aois():
    """Lista todos los AOI disponibles con sus detalles"""
    aoi_dir = Path("aoi")
    if not aoi_dir.exists():
        print(f"{Colors.RED}✗ No existe el directorio aoi/{Colors.NC}")
        return []

    aoi_files = sorted(aoi_dir.glob("*.geojson"))

    print(f"\n{Colors.CYAN}{Colors.BOLD}{'=' * 80}{Colors.NC}")
    print(f"{Colors.CYAN}{Colors.BOLD}AOI DISPONIBLES ({len(aoi_files)} total){Colors.NC}")
    print(f"{Colors.CYAN}{Colors.BOLD}{'=' * 80}{Colors.NC}\n")

    for i, aoi_file in enumerate(aoi_files, 1):
        try:
            with open(aoi_file, 'r') as f:
                data = json.load(f)
                name = data.get('name', aoi_file.stem)
                # Intentar obtener el área si está disponible
                if 'features' in data and len(data['features']) > 0:
                    props = data['features'][0].get('properties', {})
                    area = props.get('area', 'N/A')
                else:
                    area = 'N/A'
        except:
            name = aoi_file.stem
            area = 'N/A'

        print(f"  {i:3d}. {Colors.BOLD}{aoi_file.stem:<40}{Colors.NC} ({name}) - Área: {area}")

        if i % 50 == 0 and i < len(aoi_files):
            input(f"\n{Colors.YELLOW}Presiona Enter para continuar...{Colors.NC}")
            print()

    print(f"\n{Colors.CYAN}{'=' * 80}{Colors.NC}\n")
    return aoi_files


def resolve_aoi_path(aoi_identifier):
    """
    Resuelve el identificador de AOI a una ruta de archivo

    Args:
        aoi_identifier: Nombre del AOI (con o sin extensión .geojson) o ruta completa

    Returns:
        Path: Ruta al archivo GeoJSON o None si no se encuentra
    """
    aoi_dir = Path("aoi")

    # Si es una ruta completa que existe, usarla directamente
    aoi_path = Path(aoi_identifier)
    if aoi_path.exists() and aoi_path.suffix == '.geojson':
        return aoi_path

    # Si tiene extensión .geojson, buscar en directorio aoi
    if aoi_identifier.endswith('.geojson'):
        aoi_path = aoi_dir / aoi_identifier
    else:
        aoi_path = aoi_dir / f"{aoi_identifier}.geojson"

    if aoi_path.exists():
        return aoi_path

    return None


def calculate_date_range(months=3):
    """
    Calcula el rango de fechas desde hoy hacia atrás

    Args:
        months: Número de meses hacia atrás

    Returns:
        tuple: (start_date, end_date) como strings YYYY-MM-DD
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=months * 30)

    return start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')


def process_single_aoi(aoi_file, config, logger):
    """
    Procesa un único AOI con la configuración especificada

    Args:
        aoi_file: Path al archivo GeoJSON
        config: Diccionario con la configuración del workflow
        logger: Logger configurado

    Returns:
        bool: True si exitoso
    """
    project_name = aoi_file.stem
    project_dir = Path("processing") / project_name

    logger.info(f"\n{Colors.MAGENTA}{'#' * 80}{Colors.NC}")
    logger.info(f"{Colors.MAGENTA}# PROCESANDO AOI: {project_name}{Colors.NC}")
    logger.info(f"{Colors.MAGENTA}{'#' * 80}{Colors.NC}\n")

    logger.info(f"Configuración:")
    logger.info(f"  AOI: {aoi_file}")
    logger.info(f"  Periodo: {config['start_date']} a {config['end_date']}")
    logger.info(f"  Satélites: {', '.join(config['satellites'])}")
    logger.info(f"  Órbita: {config['orbit_direction']}")
    logger.info(f"  Tipo órbita: {config['orbit_type']}")
    logger.info("")

    try:
        # Verificar si el proyecto existe y preguntar si limpiar
        if workflow.check_project_exists(project_name) and not config.get('skip_existing'):
            if config.get('clean_existing'):
                logger.info(f"{Colors.YELLOW}Limpiando proyecto existente...{Colors.NC}")
                workflow.clean_project_directory(project_dir)
            else:
                logger.info(f"{Colors.YELLOW}⚠️  Proyecto ya existe, continuando con datos existentes{Colors.NC}")

        # Crear directorio si no existe
        project_dir.mkdir(parents=True, exist_ok=True)

        # PASO 1: Descargar órbitas
        if config['download']:
            logger.info(f"\n{Colors.BLUE}Descargando órbitas...{Colors.NC}")
            workflow.download_orbits(
                start_date=config['start_date'],
                end_date=config['end_date'],
                satellites=config['satellites'],
                orbit_type=config['orbit_type'],
                log=logger
            )

            # PASO 2: Descargar productos SLC
            logger.info(f"\n{Colors.BLUE}Descargando productos SLC...{Colors.NC}")
            workflow.download_products(
                aoi_file=aoi_file,
                start_date=config['start_date'],
                end_date=config['end_date'],
                product_type='SLC',
                satellites=config['satellites'],
                orbit_direction=config['orbit_direction'],
                orbit_precision=config['orbit_type'],
                use_interactive=False,
                log=logger
            )

        # PASO 3: Crear proyecto AOI
        logger.info(f"\n{Colors.BLUE}Creando proyecto AOI...{Colors.NC}")
        if not workflow.create_aoi_project(aoi_file, project_name, log=logger):
            logger.error(f"{Colors.RED}✗ Error creando proyecto AOI{Colors.NC}")
            return False

        # Determinar qué órbitas procesar
        orbits_to_process = []
        if config['orbit_direction'] == 'BOTH':
            orbits_to_process = ['DESCENDING', 'ASCENDING']
        else:
            orbits_to_process = [config['orbit_direction']]

        # PASO 4 & 5: Para cada órbita, generar configuraciones y procesar
        overall_success = True
        for orbit_dir in orbits_to_process:
            logger.info(f"\n{Colors.MAGENTA}{'~' * 80}{Colors.NC}")
            logger.info(f"{Colors.MAGENTA}ÓRBITA: {orbit_dir}{Colors.NC}")
            logger.info(f"{Colors.MAGENTA}{'~' * 80}{Colors.NC}")

            # Generar configuraciones
            if not workflow.generate_product_configurations(project_name, aoi_file, orbit_direction=orbit_dir, log=logger):
                logger.error(f"{Colors.RED}✗ Error generando configuraciones {orbit_dir}{Colors.NC}")
                overall_success = False
                continue

            # Procesar series
            if not workflow.run_processing(project_name, orbit_direction=orbit_dir, log=logger):
                overall_success = False

        # PASO 5: Recorte urbano (si procesamiento exitoso)
        if overall_success:
            logger.info(f"\n{Colors.BLUE}{'=' * 80}{Colors.NC}")
            logger.info(f"{Colors.BLUE}RECORTE A SUELO URBANO{Colors.NC}")
            logger.info(f"{Colors.BLUE}{'=' * 80}{Colors.NC}\n")
            
            mcc_file = Path("data/cobertes-sol-v1r0-2023.gpkg")
            
            if mcc_file.exists():
                logger.info(f"{Colors.GREEN}✓ MCC encontrado{Colors.NC}")
                
                try:
                    import subprocess
                    result = subprocess.run(
                        ["bash", "scripts/workflow_urban_crop.sh", str(project_dir), str(mcc_file)],
                        cwd=Path.cwd(),
                        capture_output=True,
                        text=True
                    )
                    
                    if result.returncode == 0:
                        logger.info(f"{Colors.GREEN}✓ Recorte urbano completado{Colors.NC}")
                        urban_dir = project_dir / "urban_products"
                        if urban_dir.exists():
                            n_products = len(list(urban_dir.rglob("*.tif")))
                            logger.info(f"{Colors.GREEN}  Productos urbanos: {n_products}{Colors.NC}")
                    else:
                        logger.warning(f"{Colors.YELLOW}⚠️  Recorte urbano con advertencias{Colors.NC}")
                except Exception as e:
                    logger.warning(f"{Colors.YELLOW}⚠️  Error en recorte urbano: {e}{Colors.NC}")
            else:
                logger.warning(f"{Colors.YELLOW}⚠️  MCC no encontrado, saltando recorte urbano{Colors.NC}")
        
        # PASO 6: Limpieza (siempre se ejecuta)
        logger.info(f"\n{Colors.BLUE}{'=' * 80}{Colors.NC}")
        logger.info(f"{Colors.BLUE}LIMPIEZA DE ARCHIVOS INTERMEDIOS{Colors.NC}")
        logger.info(f"{Colors.BLUE}{'=' * 80}{Colors.NC}\n")
        
        try:
            import subprocess
            result = subprocess.run(
                ["python3", "scripts/cleanup_after_urban_crop.py", str(project_dir)],
                cwd=Path.cwd(),
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                logger.info(f"{Colors.GREEN}✓ Limpieza completada{Colors.NC}")
                if "liberado:" in result.stdout:
                    for line in result.stdout.split('\n'):
                        if "liberado:" in line:
                            logger.info(f"{Colors.GREEN}  {line.strip()}{Colors.NC}")
                            break
            else:
                logger.warning(f"{Colors.YELLOW}⚠️  Limpieza con advertencias{Colors.NC}")
        except Exception as e:
            logger.warning(f"{Colors.YELLOW}⚠️  Error en limpieza: {e}{Colors.NC}")

        # Resumen
        workflow.print_summary(project_name, overall_success, log=logger)

        return overall_success

    except Exception as e:
        logger.error(f"\n{Colors.RED}✗ Error procesando AOI {project_name}: {e}{Colors.NC}")
        import traceback
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Ejecuta el workflow completo para múltiples AOI con configuración unificada',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos de uso:

  # Procesar varios AOI por nombre
  %(prog)s barcelona girona tarragona

  # Desde archivo de texto (un AOI por línea)
  %(prog)s --from-file aoi_list.txt

  # Últimos 6 meses con S1A
  %(prog)s --months 6 --satellite S1A barcelona

  # Órbita ascendente con RESORB
  %(prog)s --orbit ASCENDING --orbit-type RESORB barcelona

  # Procesar ambas órbitas
  %(prog)s --orbit BOTH barcelona

  # Saltar descarga (usar datos existentes)
  %(prog)s --skip-download barcelona

  # Limpiar proyectos existentes antes de procesar
  %(prog)s --clean-existing barcelona girona

  # Ver todos los AOI disponibles
  %(prog)s --list
        """
    )

    # Argumentos de entrada
    parser.add_argument('aois', nargs='*',
                       help='Nombres de AOI a procesar (sin extensión .geojson)')
    parser.add_argument('--from-file', '-f', metavar='FILE',
                       help='Archivo de texto con lista de AOI (uno por línea)')
    parser.add_argument('--list', '-l', action='store_true',
                       help='Listar todos los AOI disponibles y salir')

    # Configuración de fechas
    parser.add_argument('--months', '-m', type=int, default=3,
                       help='Número de meses hacia atrás desde hoy (default: 3)')
    parser.add_argument('--start-date', metavar='YYYY-MM-DD',
                       help='Fecha de inicio manual (sobrescribe --months)')
    parser.add_argument('--end-date', metavar='YYYY-MM-DD',
                       help='Fecha de fin manual (default: hoy)')

    # Configuración de satélite y órbitas
    parser.add_argument('--satellite', '-s', action='append',
                       choices=['S1A', 'S1C'], default=None,
                       help='Satélites a usar (default: S1C). Puede especificarse múltiples veces')
    parser.add_argument('--orbit', '-o', choices=['DESCENDING', 'ASCENDING', 'BOTH'],
                       default='DESCENDING',
                       help='Dirección de órbita (default: DESCENDING)')
    parser.add_argument('--orbit-type', choices=['POEORB', 'RESORB'],
                       default='POEORB',
                       help='Tipo de órbita (default: POEORB - precisas)')

    # Opciones de procesamiento
    parser.add_argument('--skip-download', action='store_true',
                       help='Saltar descarga de productos (usar solo datos existentes)')
    parser.add_argument('--clean-existing', action='store_true',
                       help='Limpiar proyectos existentes antes de procesar')
    parser.add_argument('--skip-existing', action='store_true',
                       help='Saltar AOI que ya tienen proyecto existente')

    # Opciones de logging
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Modo verbose (más detalles en logs)')

    args = parser.parse_args()

    # Si se solicita listar AOI
    if args.list:
        list_available_aois()
        return 0

    # Validar que se proporcionaron AOI
    aoi_list = []

    if args.from_file:
        # Leer desde archivo
        try:
            with open(args.from_file, 'r') as f:
                aoi_list = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        except Exception as e:
            print(f"{Colors.RED}✗ Error leyendo archivo {args.from_file}: {e}{Colors.NC}")
            return 1

    if args.aois:
        aoi_list.extend(args.aois)

    if not aoi_list:
        parser.print_help()
        print(f"\n{Colors.YELLOW}⚠️  Debes especificar AOI a procesar{Colors.NC}")
        print(f"Usa --list para ver los AOI disponibles\n")
        return 1

    # Configurar fechas
    if args.start_date and args.end_date:
        start_date = args.start_date
        end_date = args.end_date
    elif args.start_date:
        start_date = args.start_date
        end_date = datetime.now().strftime('%Y-%m-%d')
    else:
        start_date, end_date = calculate_date_range(args.months)

    # Configurar satélites
    satellites = args.satellite if args.satellite else ['S1C']

    # Crear configuración del workflow
    config = {
        'start_date': start_date,
        'end_date': end_date,
        'satellites': satellites,
        'orbit_direction': args.orbit,
        'orbit_type': args.orbit_type,
        'download': not args.skip_download,
        'clean_existing': args.clean_existing,
        'skip_existing': args.skip_existing
    }

    # Configurar logger global
    logger = LoggerConfig.setup_aoi_logger(
        aoi_project_dir=".",
        log_name="batch_workflow"
    )

    # Banner inicial
    print(f"\n{Colors.CYAN}{Colors.BOLD}{'=' * 80}{Colors.NC}")
    print(f"{Colors.CYAN}{Colors.BOLD}PROCESAMIENTO BATCH DE MÚLTIPLES AOI{Colors.NC}")
    print(f"{Colors.CYAN}{Colors.BOLD}{'=' * 80}{Colors.NC}\n")

    print(f"Configuración del batch:")
    print(f"  AOI a procesar: {Colors.BOLD}{len(aoi_list)}{Colors.NC}")
    print(f"  Periodo: {Colors.BOLD}{start_date}{Colors.NC} a {Colors.BOLD}{end_date}{Colors.NC}")
    print(f"  Satélites: {Colors.BOLD}{', '.join(satellites)}{Colors.NC}")
    print(f"  Órbita: {Colors.BOLD}{args.orbit}{Colors.NC}")
    print(f"  Tipo órbita: {Colors.BOLD}{args.orbit_type}{Colors.NC}")
    print(f"  Descargar productos: {Colors.BOLD}{'Sí' if config['download'] else 'No'}{Colors.NC}")

    print(f"\n{Colors.BOLD}AOI en la cola:{Colors.NC}")
    for i, aoi_name in enumerate(aoi_list, 1):
        print(f"  {i}. {aoi_name}")

    print(f"\n{Colors.YELLOW}¿Continuar con el procesamiento? (y/N): {Colors.NC}", end='')
    confirm = input().strip().lower()

    if confirm != 'y':
        print(f"\n{Colors.YELLOW}❌ Cancelado por el usuario{Colors.NC}\n")
        return 0

    # Resolver rutas de AOI
    aoi_files = []
    for aoi_name in aoi_list:
        aoi_path = resolve_aoi_path(aoi_name)
        if aoi_path:
            aoi_files.append(aoi_path)
        else:
            logger.warning(f"{Colors.YELLOW}⚠️  AOI no encontrado: {aoi_name}{Colors.NC}")

    if not aoi_files:
        print(f"\n{Colors.RED}✗ No se encontraron archivos AOI válidos{Colors.NC}\n")
        return 1

    print(f"\n{Colors.GREEN}✓ Se procesarán {len(aoi_files)} AOI{Colors.NC}\n")

    # Procesar cada AOI
    results = {}
    total = len(aoi_files)

    for i, aoi_file in enumerate(aoi_files, 1):
        print(f"\n{Colors.CYAN}{Colors.BOLD}{'=' * 80}{Colors.NC}")
        print(f"{Colors.CYAN}{Colors.BOLD}PROGRESO: {i}/{total} - {aoi_file.stem}{Colors.NC}")
        print(f"{Colors.CYAN}{Colors.BOLD}{'=' * 80}{Colors.NC}\n")

        # Verificar si se debe saltar
        if config['skip_existing'] and workflow.check_project_exists(aoi_file.stem):
            logger.info(f"{Colors.YELLOW}⏭️  Saltando {aoi_file.stem} (proyecto ya existe){Colors.NC}")
            results[aoi_file.stem] = 'SKIPPED'
            continue

        success = process_single_aoi(aoi_file, config, logger)
        results[aoi_file.stem] = 'SUCCESS' if success else 'FAILED'

    # Resumen final
    print(f"\n{Colors.CYAN}{Colors.BOLD}{'=' * 80}{Colors.NC}")
    print(f"{Colors.CYAN}{Colors.BOLD}RESUMEN FINAL DEL BATCH{Colors.NC}")
    print(f"{Colors.CYAN}{Colors.BOLD}{'=' * 80}{Colors.NC}\n")

    success_count = sum(1 for r in results.values() if r == 'SUCCESS')
    failed_count = sum(1 for r in results.values() if r == 'FAILED')
    skipped_count = sum(1 for r in results.values() if r == 'SKIPPED')

    print(f"Total procesados: {total}")
    print(f"  {Colors.GREEN}✓ Exitosos: {success_count}{Colors.NC}")
    print(f"  {Colors.RED}✗ Fallidos: {failed_count}{Colors.NC}")
    print(f"  {Colors.YELLOW}⏭️  Saltados: {skipped_count}{Colors.NC}")

    print(f"\nDetalle por AOI:")
    for aoi_name, result in results.items():
        if result == 'SUCCESS':
            status = f"{Colors.GREEN}✓ EXITOSO{Colors.NC}"
        elif result == 'FAILED':
            status = f"{Colors.RED}✗ FALLIDO{Colors.NC}"
        else:
            status = f"{Colors.YELLOW}⏭️  SALTADO{Colors.NC}"

        print(f"  {aoi_name:<40} {status}")

    print(f"\n{Colors.CYAN}{'=' * 80}{Colors.NC}\n")

    # Código de salida
    if failed_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}⚠️  Procesamiento interrumpido por el usuario{Colors.NC}\n")
        sys.exit(130)
    except Exception as e:
        print(f"\n{Colors.RED}ERROR: {str(e)}{Colors.NC}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)
