#!/usr/bin/env python3
"""
Script: migrate_existing_products.py
Descripción: Migra productos InSAR/Polarimétricos existentes del workspace al repositorio
             y los reemplaza por symlinks para ahorrar espacio

Este script:
1. Verifica si el producto ya existe en el repositorio compartido
2. Si NO existe: lo copia al repositorio
3. Elimina el archivo local
4. Crea symlink desde workspace → repositorio

Uso:
    python scripts/migrate_existing_products.py <workspace_path> [--dry-run]

Ejemplos:
    # Simulación (no hace cambios)
    python scripts/migrate_existing_products.py processing/arenys_de_munt/insar_desc_iw1 --dry-run

    # Ejecución real
    python scripts/migrate_existing_products.py processing/arenys_de_munt/insar_desc_iw1
"""

import os
import sys
import shutil
import argparse
from pathlib import Path
from typing import Optional

# Añadir directorio scripts/ al path para importar módulos locales
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from insar_repository import InSARRepository
from logging_utils import LoggerConfig

logger = None


def extract_metadata_from_workspace(workspace_path: Path) -> dict:
    """
    Extrae metadatos del workspace (órbita, subswath, track)
    leyendo config.txt o el nombre del directorio

    Args:
        workspace_path: Ruta al workspace (ej: processing/arenys_de_munt/insar_desc_iw1)

    Returns:
        dict con: orbit_direction, subswath, track
    """
    # Estrategia 1: Leer config.txt
    config_file = workspace_path / "config.txt"
    if config_file.exists():
        orbit_direction = None
        subswath = None
        track = None

        with open(config_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('ORBIT_DIRECTION='):
                    orbit_direction = line.split('=')[1].strip().upper()
                elif line.startswith('SUBSWATH='):
                    subswath = line.split('=')[1].strip().upper()
                elif line.startswith('TRACK='):
                    track = int(line.split('=')[1].strip())

        if orbit_direction and subswath and track:
            return {
                'orbit_direction': orbit_direction,
                'subswath': subswath,
                'track': track
            }

    # Estrategia 2: Parsear nombre del directorio
    # Formato esperado: insar_desc_iw1 o insar_asce_iw2
    workspace_name = workspace_path.name

    if 'desc' in workspace_name.lower():
        orbit_direction = 'DESCENDING'
    elif 'asce' in workspace_name.lower():
        orbit_direction = 'ASCENDING'
    else:
        raise ValueError(f"No se pudo determinar dirección de órbita de: {workspace_name}")

    # Extraer subswath (IW1, IW2, IW3)
    if 'iw1' in workspace_name.lower():
        subswath = 'IW1'
    elif 'iw2' in workspace_name.lower():
        subswath = 'IW2'
    elif 'iw3' in workspace_name.lower():
        subswath = 'IW3'
    else:
        raise ValueError(f"No se pudo determinar subswath de: {workspace_name}")

    # Estrategia 3: Extraer track del primer producto SLC
    slc_dir = workspace_path / "slc"
    if slc_dir.exists():
        slc_products = list(slc_dir.glob("S1*.SAFE"))
        if slc_products:
            repository = InSARRepository()
            track = repository.extract_track_from_slc(slc_products[0].name)
            if track:
                return {
                    'orbit_direction': orbit_direction,
                    'subswath': subswath,
                    'track': track
                }

    raise ValueError(f"No se pudo extraer track del workspace: {workspace_path}")


def migrate_single_product(
    local_file: Path,
    repository: InSARRepository,
    orbit_direction: str,
    subswath: str,
    track: int,
    pair_type: str,
    dry_run: bool = False
) -> dict:
    """
    Migra un producto individual al repositorio

    Returns:
        dict con estadísticas: copied, replaced_by_symlink, already_symlink, error
    """
    stats = {'copied': 0, 'replaced_by_symlink': 0, 'already_symlink': 0, 'error': 0}

    # 1. Si ya es symlink, skip
    if local_file.is_symlink():
        logger.debug(f"  ✓ Ya es symlink: {local_file.name}")
        stats['already_symlink'] += 1
        return stats

    # 2. Determinar destino en repositorio
    track_dir = repository.get_track_dir(orbit_direction, subswath, track)
    dest_file = track_dir / "insar" / pair_type / local_file.name
    local_data = local_file.with_suffix('.data')
    dest_data = dest_file.with_suffix('.data')

    # 3. Verificar que archivo local existe y es válido
    if not local_file.exists():
        logger.warning(f"  ✗ Archivo local no existe: {local_file}")
        stats['error'] += 1
        return stats

    if not local_data.exists():
        logger.warning(f"  ✗ Carpeta .data no existe: {local_data}")
        stats['error'] += 1
        return stats

    # 4. Copiar al repositorio si no existe
    try:
        if not dest_file.exists():
            if dry_run:
                logger.info(f"  [DRY-RUN] Copiaría: {local_file.name} → repositorio")
            else:
                logger.info(f"  📦 Copiando: {local_file.name}")
                # Asegurar que existe el directorio destino
                dest_file.parent.mkdir(parents=True, exist_ok=True)

                # Copiar .dim
                shutil.copy2(local_file, dest_file)

                # Copiar .data/
                if dest_data.exists():
                    shutil.rmtree(dest_data)
                shutil.copytree(local_data, dest_data, dirs_exist_ok=True)

                # Actualizar metadata
                metadata = repository.load_metadata(orbit_direction, subswath, track)
                product_info = repository._extract_insar_info(dest_file, pair_type)

                # Verificar que no esté ya en metadata (evitar duplicados)
                existing_files = [p['file'] for p in metadata.get('insar_products', [])]
                if product_info['file'] not in existing_files:
                    metadata['insar_products'].append(product_info)
                    repository.save_metadata(orbit_direction, subswath, track, metadata)

                logger.info(f"    ✓ Copiado al repositorio")
                stats['copied'] += 1
        else:
            logger.debug(f"  ✓ Ya existe en repositorio: {local_file.name}")

        # 5. Verificar integridad de la copia
        if not dry_run:
            if not dest_file.exists() or not dest_data.exists():
                logger.error(f"  ✗ Copia al repositorio falló - manteniendo local: {local_file.name}")
                stats['error'] += 1
                return stats

            # Verificar tamaño .dim
            local_size = local_file.stat().st_size
            dest_size = dest_file.stat().st_size
            if local_size != dest_size:
                logger.warning(f"  ⚠️  Tamaños difieren ({local_size} vs {dest_size}) - manteniendo local")
                stats['error'] += 1
                return stats

        # 6. Reemplazar local por symlink
        if dry_run:
            logger.info(f"  [DRY-RUN] Eliminaría local y crearía symlink: {local_file.name}")
        else:
            logger.debug(f"  🗑️  Eliminando archivos locales...")

            # Eliminar .dim
            local_file.unlink()
            logger.debug(f"    ✓ Eliminado: {local_file.name}")

            # Eliminar .data/
            shutil.rmtree(local_data)
            logger.debug(f"    ✓ Eliminado: {local_data.name}/")

            # Crear symlinks
            local_file.symlink_to(dest_file.absolute())
            local_data.symlink_to(dest_data.absolute())

            logger.info(f"  🔗 Symlinks creados: {local_file.name}")
            stats['replaced_by_symlink'] += 1

    except Exception as e:
        logger.error(f"  ✗ Error migrando {local_file.name}: {e}")
        stats['error'] += 1

    return stats


def migrate_workspace(workspace_path: Path, dry_run: bool = False) -> bool:
    """
    Migra todos los productos de un workspace al repositorio

    Args:
        workspace_path: Ruta al workspace (ej: processing/arenys_de_munt/insar_desc_iw1)
        dry_run: Si True, solo simula sin hacer cambios

    Returns:
        True si exitoso
    """
    workspace_path = Path(workspace_path).resolve()

    if not workspace_path.exists():
        logger.error(f"✗ Workspace no existe: {workspace_path}")
        return False

    logger.info("=" * 80)
    logger.info("MIGRACIÓN DE PRODUCTOS AL REPOSITORIO")
    logger.info("=" * 80)
    logger.info(f"Workspace: {workspace_path}")

    if dry_run:
        logger.info("🔍 MODO SIMULACIÓN (no se harán cambios)")
    logger.info("")

    # Extraer metadata del workspace
    try:
        metadata = extract_metadata_from_workspace(workspace_path)
        orbit_direction = metadata['orbit_direction']
        subswath = metadata['subswath']
        track = metadata['track']

        logger.info(f"Metadatos detectados:")
        logger.info(f"  Órbita: {orbit_direction}")
        logger.info(f"  Subswath: {subswath}")
        logger.info(f"  Track: {track}")
        logger.info("")
    except Exception as e:
        logger.error(f"✗ Error extrayendo metadatos: {e}")
        return False

    # Inicializar repositorio
    repository = InSARRepository()

    # Asegurar estructura en repositorio
    if not dry_run:
        repository.ensure_track_structure(orbit_direction, subswath, track)

    # Estadísticas globales
    total_stats = {
        'copied': 0,
        'replaced_by_symlink': 0,
        'already_symlink': 0,
        'error': 0
    }

    # Migrar productos InSAR SHORT
    insar_short_dir = workspace_path / "insar" / "short"
    if insar_short_dir.exists():
        logger.info("📁 Migrando productos InSAR SHORT...")
        for dim_file in sorted(insar_short_dir.glob("*.dim")):
            stats = migrate_single_product(
                dim_file, repository, orbit_direction, subswath, track, "short", dry_run
            )
            for key in total_stats:
                total_stats[key] += stats[key]
        logger.info("")

    # Migrar productos InSAR LONG
    insar_long_dir = workspace_path / "insar" / "long"
    if insar_long_dir.exists():
        logger.info("📁 Migrando productos InSAR LONG...")
        for dim_file in sorted(insar_long_dir.glob("*.dim")):
            stats = migrate_single_product(
                dim_file, repository, orbit_direction, subswath, track, "long", dry_run
            )
            for key in total_stats:
                total_stats[key] += stats[key]
        logger.info("")

    # Resumen
    logger.info("=" * 80)
    logger.info("RESUMEN DE MIGRACIÓN")
    logger.info("=" * 80)
    logger.info(f"Productos copiados al repositorio: {total_stats['copied']}")
    logger.info(f"Productos reemplazados por symlinks: {total_stats['replaced_by_symlink']}")
    logger.info(f"Productos ya eran symlinks: {total_stats['already_symlink']}")
    logger.info(f"Errores: {total_stats['error']}")
    logger.info("")

    if dry_run:
        logger.info("✓ Simulación completada (ningún cambio realizado)")
    else:
        if total_stats['error'] == 0:
            logger.info("✓ Migración completada exitosamente")
        else:
            logger.warning(f"⚠️  Migración completada con {total_stats['error']} errores")

    logger.info("")
    return total_stats['error'] == 0


def main():
    parser = argparse.ArgumentParser(
        description='Migra productos InSAR existentes del workspace al repositorio compartido',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  # Simulación (sin hacer cambios)
  python scripts/migrate_existing_products.py processing/arenys_de_munt/insar_desc_iw1 --dry-run

  # Ejecución real
  python scripts/migrate_existing_products.py processing/arenys_de_munt/insar_desc_iw1
        """
    )

    parser.add_argument(
        'workspace_path',
        type=str,
        help='Ruta al workspace a migrar (ej: processing/arenys_de_munt/insar_desc_iw1)'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Simular sin hacer cambios reales'
    )

    args = parser.parse_args()

    # Configurar logger
    global logger
    workspace_path = Path(args.workspace_path)
    logger = LoggerConfig.setup_aoi_logger(
        aoi_project_dir=str(workspace_path.parent),
        log_name="migrate_products"
    )

    # Ejecutar migración
    success = migrate_workspace(workspace_path, dry_run=args.dry_run)

    return 0 if success else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n⚠️  Migración interrumpida por el usuario")
        sys.exit(130)
    except Exception as e:
        print(f"\n✗ ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
