#!/usr/bin/env python3
"""
Script: cleanup_slc_repository.py
Descripción: Limpia archivos SLC raw que ya han sido completamente procesados

Estrategia:
- Analiza productos InSAR en el repositorio para identificar SLC usados
- Mantiene 3 primeros + 3 últimos SLC de cada track
- Solo elimina SLC intermedios que estén completamente procesados
- Verifica que productos InSAR existan físicamente antes de eliminar
- Solo elimina si SLC está procesado en TODOS los tracks donde aparece

Uso:
    # Modo reporte (solo análisis)
    python scripts/cleanup_slc_repository.py --dry-run

    # Ejecutar eliminación
    python scripts/cleanup_slc_repository.py --delete

    # Configurar extremos
    python scripts/cleanup_slc_repository.py --keep-first 3 --keep-last 3 --dry-run
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Importar módulos locales
sys.path.append(os.path.dirname(__file__))
from insar_repository import InSARRepository
from logging_utils import LoggerConfig

# Logger se configurará en main()
logger = None


def analyze_repository_tracks(repo_base_dir: Path) -> Dict[int, Set[str]]:
    """
    Analiza todos los tracks en el repositorio y extrae fechas SLC usadas.

    Args:
        repo_base_dir: Directorio base del repositorio

    Returns:
        Dict[track_number, Set[fechas_YYYYMMDD]]
    """
    repository = InSARRepository(repo_base_dir=repo_base_dir)
    track_dates = defaultdict(set)

    logger.info("Analizando repositorio de productos procesados...")

    # Buscar todos los directorios de tracks
    for orbit_subswath_dir in repo_base_dir.iterdir():
        if not orbit_subswath_dir.is_dir():
            continue

        for track_dir in orbit_subswath_dir.iterdir():
            if not track_dir.is_dir() or not track_dir.name.startswith('t'):
                continue

            # Extraer track number
            track_match = re.match(r't(\d+)', track_dir.name)
            if not track_match:
                continue

            track_number = int(track_match.group(1))

            # Leer metadata
            metadata_file = track_dir / "metadata.json"
            if not metadata_file.exists():
                logger.warning(f"  ⚠️  No existe metadata.json en {track_dir}")
                continue

            try:
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)

                # Extraer fechas de productos InSAR
                for product in metadata.get('insar_products', []):
                    master_date = product.get('master_date')
                    slave_date = product.get('slave_date')
                    if master_date:
                        track_dates[track_number].add(master_date)
                    if slave_date:
                        track_dates[track_number].add(slave_date)

                # Extraer fechas de productos polarimétricos
                for product in metadata.get('polarimetry_products', []):
                    date = product.get('date')
                    if date:
                        track_dates[track_number].add(date)

                logger.info(f"  ✓ Track {track_number:03d}: {len(track_dates[track_number])} fechas usadas")

            except Exception as e:
                logger.error(f"  ✗ Error leyendo metadata de {track_dir}: {e}")
                continue

    return dict(track_dates)


def scan_slc_files(slc_dir: Path, repository: InSARRepository) -> Dict[int, Dict[str, List[Path]]]:
    """
    Escanea directorio SLC y organiza por track y fecha.

    Args:
        slc_dir: Directorio con archivos .SAFE
        repository: Instancia de InSARRepository para calcular tracks

    Returns:
        Dict[track_number, Dict[fecha_YYYYMMDD, List[Path_to_SAFE]]]
    """
    track_slc_map = defaultdict(lambda: defaultdict(list))

    logger.info(f"\nEscaneando archivos SLC en {slc_dir}...")

    slc_files = list(slc_dir.glob("*.SAFE"))
    logger.info(f"  Archivos .SAFE encontrados: {len(slc_files)}")

    for slc_file in slc_files:
        # Extraer fecha
        date_match = re.search(r'S1[ABC]_IW_SLC__1S\w+_(\d{8})T', slc_file.name)
        if not date_match:
            logger.warning(f"  ⚠️  No se pudo extraer fecha de {slc_file.name}")
            continue

        slc_date = date_match.group(1)

        # Calcular track
        track_number = repository.extract_track_from_slc(slc_file.name)
        if not track_number:
            logger.warning(f"  ⚠️  No se pudo calcular track de {slc_file.name}")
            continue

        # Registrar
        track_slc_map[track_number][slc_date].append(slc_file)

    # Resumen
    total_tracks = len(track_slc_map)
    total_dates = sum(len(dates) for dates in track_slc_map.values())
    logger.info(f"  ✓ {total_dates} fechas únicas en {total_tracks} tracks")

    return dict(track_slc_map)


def verify_insar_products_exist(repo_base_dir: Path, track_number: int, slc_date: str) -> bool:
    """
    Verifica que TODOS los pares InSAR esperados que usan este SLC existan en disco.

    LÓGICA ROBUSTA:
    1. Obtiene todas las fechas SLC disponibles para este track
    2. Calcula TODOS los pares esperados (SHORT y LONG) que deberían usar este SLC
    3. Verifica que TODOS esos pares existan físicamente en TODAS las subswaths
    4. Solo retorna True si el SLC está COMPLETAMENTE procesado en todos los pares esperados

    Pares esperados para un SLC en medio de la serie:
    - 2 SHORT: como master (date→date+1) y como slave (date-1→date)
    - 2 LONG: como master (date→date+2) y como slave (date-2→date)
    - Total: 4 pares (si es fecha intermedia)

    Args:
        repo_base_dir: Directorio base del repositorio
        track_number: Número de track
        slc_date: Fecha SLC en formato YYYYMMDD

    Returns:
        True si TODOS los pares esperados que usan este SLC existen en todas las subswaths
    """
    from datetime import datetime

    # Buscar TODOS los directorios del track (puede estar en múltiples subswaths)
    track_dirs = list(repo_base_dir.glob(f"*/t{track_number:03d}"))
    if not track_dirs:
        return False

    # Obtener todas las fechas SLC disponibles para calcular pares esperados
    all_slc_dates = set()
    existing_pairs = {'short': set(), 'long': set()}

    for track_dir in track_dirs:
        metadata_file = track_dir / "metadata.json"
        if not metadata_file.exists():
            continue

        try:
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)

            # Recopilar fechas SLC y pares existentes
            for product in metadata.get('insar_products', []):
                master_date = product.get('master_date')
                slave_date = product.get('slave_date')
                pair_type = product.get('pair_type', product.get('type', 'short'))

                if master_date:
                    all_slc_dates.add(master_date)
                if slave_date:
                    all_slc_dates.add(slave_date)

                if master_date and slave_date:
                    existing_pairs[pair_type].add((master_date, slave_date))

        except Exception as e:
            if logger:
                logger.debug(f"    ✗ Error leyendo metadata {track_dir}: {e}")
            return False

    if not all_slc_dates:
        return False

    # Convertir a lista ordenada
    slc_dates_sorted = sorted(list(all_slc_dates))

    # Calcular TODOS los pares esperados (SHORT y LONG)
    expected_pairs = {'short': [], 'long': []}

    # Pares SHORT: consecutivos (i → i+1)
    for i in range(len(slc_dates_sorted) - 1):
        expected_pairs['short'].append((slc_dates_sorted[i], slc_dates_sorted[i + 1]))

    # Pares LONG: salto de 1 (i → i+2)
    for i in range(len(slc_dates_sorted) - 2):
        expected_pairs['long'].append((slc_dates_sorted[i], slc_dates_sorted[i + 2]))

    # Filtrar pares que involucran el SLC a verificar
    expected_pairs_for_slc = {'short': [], 'long': []}
    for pair_type in ['short', 'long']:
        for master, slave in expected_pairs[pair_type]:
            if slc_date in [master, slave]:
                expected_pairs_for_slc[pair_type].append((master, slave))

    total_expected = len(expected_pairs_for_slc['short']) + len(expected_pairs_for_slc['long'])

    if total_expected == 0:
        # Este SLC no debería tener pares (fecha fuera de rango?)
        return False

    # Verificar que TODOS los pares esperados existan en TODAS las subswaths
    for track_dir in track_dirs:
        metadata_file = track_dir / "metadata.json"
        if not metadata_file.exists():
            continue

        try:
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)

            # Verificar cada par esperado
            for pair_type in ['short', 'long']:
                for master, slave in expected_pairs_for_slc[pair_type]:
                    # Buscar este par en los productos
                    pair_found = False

                    for product in metadata.get('insar_products', []):
                        prod_master = product.get('master_date')
                        prod_slave = product.get('slave_date')
                        prod_type = product.get('pair_type', product.get('type', 'short'))

                        if prod_master == master and prod_slave == slave and prod_type == pair_type:
                            pair_found = True
                            product_file = track_dir / product['file']

                            if not product_file.exists():
                                # Falta un producto esperado - NO se puede eliminar el SLC
                                if logger:
                                    logger.debug(f"    ⚠️  Par esperado no existe: {product_file}")
                                    logger.debug(f"    → SLC {slc_date} todavía necesario para {master}→{slave} ({pair_type})")
                                return False
                            break

                    if not pair_found:
                        # Este par esperado no se ha procesado aún - NO eliminar SLC
                        if logger:
                            logger.debug(f"    ⚠️  Par esperado no procesado: {master}→{slave} ({pair_type})")
                            logger.debug(f"    → SLC {slc_date} todavía necesario en {track_dir.parent.name}/{track_dir.name}")
                        return False

            # Verificar productos polarimétricos
            for product in metadata.get('polarimetry_products', []):
                if product.get('date') == slc_date:
                    product_file = track_dir / product['file']
                    if not product_file.exists():
                        if logger:
                            logger.debug(f"    ⚠️  Producto polarimétrico no existe: {product_file}")
                        return False

        except Exception as e:
            if logger:
                logger.debug(f"    ✗ Error verificando {track_dir}: {e}")
            return False

    # Si llegamos aquí, TODOS los pares esperados existen en TODAS las subswaths
    if logger:
        logger.debug(f"    ✓ Todos los pares esperados ({total_expected}) verificados en {len(track_dirs)} subswath(s) para {slc_date}")
    return True


def identify_deletable_slc(
    track_slc_map: Dict[int, Dict[str, List[Path]]],
    track_dates_used: Dict[int, Set[str]],
    repo_base_dir: Path,
    keep_first: int = 3,
    keep_last: int = 3
) -> Dict[int, Dict[str, List[Tuple[Path, str]]]]:
    """
    Identifica SLC que pueden ser eliminados de forma segura.

    Args:
        track_slc_map: Mapa de tracks → fechas → archivos SLC
        track_dates_used: Mapa de tracks → fechas usadas en productos
        repo_base_dir: Directorio base del repositorio
        keep_first: Número de primeros SLC a mantener
        keep_last: Número de últimos SLC a mantener

    Returns:
        Dict[track_number, Dict['keep'|'delete', List[(Path, reason)]]]
    """
    result = {}

    logger.info(f"\nIdentificando SLC eliminables...")
    logger.info(f"  Regla: mantener {keep_first} primeros + {keep_last} últimos")

    for track_number in sorted(track_slc_map.keys()):
        slc_dates = track_slc_map[track_number]
        dates_used = track_dates_used.get(track_number, set())

        if not dates_used:
            logger.info(f"  Track {track_number:03d}: Sin productos procesados, mantener todos los SLC")
            continue

        # Ordenar fechas
        sorted_dates = sorted(slc_dates.keys())
        total_dates = len(sorted_dates)

        keep_list = []
        delete_list = []

        for idx, date in enumerate(sorted_dates):
            slc_files = slc_dates[date]

            # Decisión: mantener o eliminar
            if idx < keep_first:
                # Primeros N
                for slc_file in slc_files:
                    keep_list.append((slc_file, f"Primero {idx+1}/{keep_first}"))

            elif idx >= total_dates - keep_last:
                # Últimos N
                pos_from_end = total_dates - idx
                for slc_file in slc_files:
                    keep_list.append((slc_file, f"Último {pos_from_end}/{keep_last}"))

            else:
                # Intermedio: verificar si está usado y productos existen
                if date not in dates_used:
                    # No está usado en productos procesados
                    for slc_file in slc_files:
                        keep_list.append((slc_file, "No procesado aún"))
                else:
                    # Está usado: verificar que productos existan
                    products_exist = verify_insar_products_exist(repo_base_dir, track_number, date)

                    if products_exist:
                        # Productos verificados: puede eliminarse
                        for slc_file in slc_files:
                            delete_list.append((slc_file, f"Procesado completamente, productos verificados"))
                    else:
                        # Productos no verificados: mantener por seguridad
                        for slc_file in slc_files:
                            keep_list.append((slc_file, "Productos no verificados"))

        result[track_number] = {
            'keep': keep_list,
            'delete': delete_list
        }

        logger.info(f"  Track {track_number:03d}: {len(keep_list)} mantener, {len(delete_list)} eliminar")

    return result


def get_directory_size(path: Path) -> float:
    """
    Calcula tamaño total de un directorio en GB.

    Args:
        path: Ruta al directorio

    Returns:
        Tamaño en GB
    """
    if not path.exists():
        return 0.0

    total_size = 0
    try:
        for item in path.rglob('*'):
            if item.is_file():
                total_size += item.stat().st_size
    except Exception:
        return 0.0

    return total_size / (1024**3)


def generate_report(
    deletable_slc: Dict[int, Dict[str, List[Tuple[Path, str]]]],
    track_slc_map: Dict[int, Dict[str, List[Path]]]
) -> str:
    """
    Genera reporte detallado de limpieza.

    Returns:
        Texto del reporte
    """
    lines = []
    lines.append("=" * 80)
    lines.append("REPORTE DE LIMPIEZA SLC - REPOSITORIO")
    lines.append("=" * 80)
    lines.append(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    total_slc = sum(len(dates) for dates in track_slc_map.values())
    total_tracks = len(track_slc_map)

    lines.append(f"Tracks analizados: {total_tracks}")
    lines.append(f"SLC totales: {total_slc}")
    lines.append("")

    grand_total_delete = 0
    grand_total_keep = 0
    grand_total_size_delete = 0.0

    for track_number in sorted(deletable_slc.keys()):
        track_data = deletable_slc[track_number]
        keep_list = track_data['keep']
        delete_list = track_data['delete']

        lines.append(f"Track t{track_number:03d}:")
        lines.append(f"  SLC totales: {len(keep_list) + len(delete_list)}")
        lines.append("")

        # Mantener
        if keep_list:
            lines.append(f"  MANTENER ({len(keep_list)} archivos):")
            for slc_file, reason in keep_list[:5]:  # Mostrar solo primeros 5
                size_gb = get_directory_size(slc_file)
                lines.append(f"    ✓ {slc_file.name[:45]:<45} ({size_gb:.1f} GB) - {reason}")
            if len(keep_list) > 5:
                lines.append(f"    ... y {len(keep_list) - 5} más")
            lines.append("")

        # Eliminar
        if delete_list:
            lines.append(f"  ELIMINAR ({len(delete_list)} archivos):")
            track_delete_size = 0.0
            for slc_file, reason in delete_list:
                size_gb = get_directory_size(slc_file)
                track_delete_size += size_gb
                lines.append(f"    → {slc_file.name[:45]:<45} ({size_gb:.1f} GB)")
                lines.append(f"       {reason}")
            lines.append(f"  Subtotal track: {len(delete_list)} SLC → {track_delete_size:.1f} GB")
            grand_total_size_delete += track_delete_size

        lines.append("")

        grand_total_delete += len(delete_list)
        grand_total_keep += len(keep_list)

    # Totales
    lines.append("=" * 80)
    lines.append("TOTALES:")
    lines.append(f"  SLC a eliminar: {grand_total_delete}")
    lines.append(f"  Espacio a liberar: {grand_total_size_delete:.1f} GB")
    lines.append(f"  SLC a mantener: {grand_total_keep}")
    lines.append("=" * 80)

    return "\n".join(lines)


def delete_slc_files(deletable_slc: Dict[int, Dict[str, List[Tuple[Path, str]]]]) -> Tuple[int, int]:
    """
    Elimina archivos SLC marcados para eliminación.

    Returns:
        (num_deleted, num_failed)
    """
    import shutil

    num_deleted = 0
    num_failed = 0

    for track_number in sorted(deletable_slc.keys()):
        delete_list = deletable_slc[track_number]['delete']

        if not delete_list:
            continue

        logger.info(f"\nEliminando SLC de track t{track_number:03d}...")

        for slc_file, reason in delete_list:
            try:
                if not slc_file.exists():
                    logger.warning(f"  ⚠️  No existe: {slc_file.name}")
                    num_failed += 1
                    continue

                size_gb = get_directory_size(slc_file)
                shutil.rmtree(slc_file)
                logger.info(f"  ✓ Eliminado: {slc_file.name} ({size_gb:.1f} GB)")
                num_deleted += 1

            except Exception as e:
                logger.error(f"  ✗ Error eliminando {slc_file.name}: {e}")
                num_failed += 1

    return num_deleted, num_failed


def main():
    global logger

    parser = argparse.ArgumentParser(
        description='Limpia archivos SLC raw que ya han sido completamente procesados',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  # Modo reporte (solo análisis)
  python scripts/cleanup_slc_repository.py --dry-run

  # Ejecutar eliminación
  python scripts/cleanup_slc_repository.py --delete

  # Configurar extremos
  python scripts/cleanup_slc_repository.py --keep-first 3 --keep-last 3 --dry-run

  # Generar JSON
  python scripts/cleanup_slc_repository.py --export cleanup_report.json --dry-run
        """
    )

    parser.add_argument('--dry-run', action='store_true',
                       help='Solo generar reporte, no eliminar archivos (default)')
    parser.add_argument('--delete', action='store_true',
                       help='Ejecutar eliminación de archivos')
    parser.add_argument('--keep-first', type=int, default=3,
                       help='Número de primeros SLC a mantener (default: 3)')
    parser.add_argument('--keep-last', type=int, default=3,
                       help='Número de últimos SLC a mantener (default: 3)')
    parser.add_argument('--export', type=str, metavar='FILE',
                       help='Exportar reporte a archivo JSON')
    parser.add_argument('--slc-dir', type=str,
                       default='data/sentinel1_slc',
                       help='Directorio con archivos SLC (default: data/sentinel1_slc)')
    parser.add_argument('--repo-dir', type=str,
                       default='data/processed_products',
                       help='Directorio del repositorio (default: data/processed_products)')

    args = parser.parse_args()

    # Configurar logger
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"cleanup_slc_{timestamp}.log"

    logger = LoggerConfig.setup_series_logger(
        series_dir=".",
        log_name=f"cleanup_slc_{timestamp}"
    )

    logger.info("=" * 80)
    logger.info("CLEANUP SLC REPOSITORY")
    logger.info("=" * 80)
    logger.info(f"Modo: {'DRY-RUN (solo reporte)' if not args.delete else 'ELIMINACIÓN ACTIVA'}")
    logger.info(f"Mantener: {args.keep_first} primeros + {args.keep_last} últimos")
    logger.info("")

    # Paths
    slc_dir = Path(args.slc_dir)
    repo_base_dir = Path(args.repo_dir)

    if not slc_dir.exists():
        logger.error(f"✗ No existe directorio SLC: {slc_dir}")
        return 1

    if not repo_base_dir.exists():
        logger.error(f"✗ No existe directorio repositorio: {repo_base_dir}")
        return 1

    # Paso 1: Analizar repositorio
    track_dates_used = analyze_repository_tracks(repo_base_dir)

    # Paso 2: Escanear SLC
    repository = InSARRepository(repo_base_dir=repo_base_dir)
    track_slc_map = scan_slc_files(slc_dir, repository)

    # Paso 3: Identificar eliminables
    deletable_slc = identify_deletable_slc(
        track_slc_map,
        track_dates_used,
        repo_base_dir,
        keep_first=args.keep_first,
        keep_last=args.keep_last
    )

    # Paso 4: Generar reporte
    report = generate_report(deletable_slc, track_slc_map)
    print("\n" + report)
    logger.info("\n" + report)

    # Paso 5: Exportar JSON si se solicita
    if args.export:
        export_data = {
            'timestamp': datetime.now().isoformat(),
            'config': {
                'keep_first': args.keep_first,
                'keep_last': args.keep_last,
                'slc_dir': str(slc_dir),
                'repo_dir': str(repo_base_dir)
            },
            'tracks': {}
        }

        for track_number, track_data in deletable_slc.items():
            export_data['tracks'][f't{track_number:03d}'] = {
                'keep': [str(f) for f, r in track_data['keep']],
                'delete': [str(f) for f, r in track_data['delete']]
            }

        with open(args.export, 'w') as f:
            json.dump(export_data, f, indent=2)

        logger.info(f"\n✓ Reporte exportado a: {args.export}")

    # Paso 6: Eliminar si se solicita
    if args.delete:
        # Confirmación
        print("\n" + "=" * 80)
        print("⚠️  ADVERTENCIA: Se eliminarán archivos SLC del disco")
        print("=" * 80)
        response = input("\n¿Confirmar eliminación? (escribir 'SI' para confirmar): ")

        if response.strip() == 'SI':
            logger.info("\n✓ Confirmación recibida, iniciando eliminación...")
            num_deleted, num_failed = delete_slc_files(deletable_slc)

            logger.info("\n" + "=" * 80)
            logger.info("RESUMEN DE ELIMINACIÓN")
            logger.info("=" * 80)
            logger.info(f"Archivos eliminados: {num_deleted}")
            logger.info(f"Errores: {num_failed}")
            logger.info("=" * 80)
        else:
            logger.info("\n✗ Eliminación cancelada por el usuario")
    else:
        print("\n💡 Para ejecutar la eliminación, usa: --delete")

    logger.info(f"\nLog guardado en: {log_file}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
