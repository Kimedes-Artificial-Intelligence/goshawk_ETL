#!/usr/bin/env python3
"""
Script para migrar productos de polarimetría existentes a la base de datos.

Los productos de polarimetría que ya están en disco pero no fueron registrados
en la BD serán agregados automáticamente.

Author: goshawk_ETL
"""

import re
import sys
from datetime import datetime
from pathlib import Path

# Add scripts to path
script_dir = Path(__file__).parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

from satelit_db.database import get_session
from satelit_db.api import SatelitDBAPI
from satelit_db.models import Product, StorageLocation


def extract_track_info_from_path(path: str):
    """
    Extrae información del track de la ruta.

    Ejemplo: /path/desc_iw2/t034/polarimetry/...
    Returns: (orbit_direction, subswath, track_number)
    """
    path_lower = path.lower()

    # Buscar orbit direction
    if '/desc_' in path_lower or '/descending_' in path_lower:
        orbit = 'DESCENDING'
    elif '/asce_' in path_lower or '/ascending_' in path_lower:
        orbit = 'ASCENDING'
    else:
        return None

    # Buscar subswath
    match = re.search(r'_(iw[123])', path_lower)
    if not match:
        return None
    subswath = match.group(1).upper()

    # Buscar track number
    match = re.search(r'/t(\d{3})', path_lower)
    if not match:
        return None
    track = int(match.group(1))

    return orbit, subswath, track


def extract_date_from_filename(filename: str):
    """
    Extrae la fecha del nombre del archivo de polarimetría.

    Ejemplo: SLC_20230429_HAAlpha.dim → 20230429
    """
    match = re.search(r'SLC_(\d{8})', filename)
    if match:
        return match.group(1)
    return None


def find_polarimetry_products(base_dir: str = "/mnt/satelit_data/processed_products"):
    """
    Busca todos los productos de polarimetría en disco.

    Returns:
        Lista de tuplas (file_path, orbit_direction, subswath, track_number, date)
    """
    base_path = Path(base_dir)

    if not base_path.exists():
        print(f"❌ Directorio base no existe: {base_dir}")
        return []

    products = []

    # Buscar todos los .dim de polarimetría
    for dim_file in base_path.rglob("*_HAAlpha.dim"):
        # Verificar que está en un directorio de polarimetría
        if 'polarimetry' not in str(dim_file).lower():
            continue

        # Extraer información del track
        track_info = extract_track_info_from_path(str(dim_file))
        if not track_info:
            print(f"⚠️  No se pudo extraer info del track: {dim_file}")
            continue

        orbit, subswath, track = track_info

        # Extraer fecha
        date_str = extract_date_from_filename(dim_file.name)
        if not date_str:
            print(f"⚠️  No se pudo extraer fecha: {dim_file}")
            continue

        products.append((str(dim_file), orbit, subswath, track, date_str))

    return products


def migrate_polarimetry_products(base_dir: str = "/mnt/satelit_data/processed_products", dry_run: bool = True):
    """
    Migra productos de polarimetría a la base de datos.

    Args:
        base_dir: Directorio base de productos procesados
        dry_run: Si True, solo muestra los cambios sin aplicarlos
    """
    print("Buscando productos de polarimetría en disco...")
    products = find_polarimetry_products(base_dir)

    print(f"\nProductos de polarimetría encontrados: {len(products)}")

    if not products:
        print("No hay productos para migrar")
        return

    migrated_count = 0
    skipped_count = 0
    error_count = 0

    with get_session() as session:
        api = SatelitDBAPI(session)

        for file_path, orbit, subswath, track, date_str in products:
            try:
                # Construir scene_id (simplificado)
                source_scene_id = f"S1_IW_SLC_{date_str}"

                # Verificar si ya existe
                polar_scene_id = f"POLAR_{source_scene_id}"
                existing = session.query(Product).filter(
                    Product.scene_id == polar_scene_id
                ).first()

                if existing:
                    skipped_count += 1
                    continue

                # Mostrar lo que se va a registrar
                print(f"{'[DRY RUN] ' if dry_run else ''}✓ {Path(file_path).name}")
                print(f"  Track: {orbit[:4]}/{subswath}/t{track:03d}")
                print(f"  Date: {date_str}")
                print(f"  Path: {file_path[:80]}...")

                if not dry_run:
                    # Parsear fecha
                    try:
                        acquisition_date = datetime.strptime(date_str, "%Y%m%d")
                    except:
                        print(f"  ⚠️  Fecha inválida: {date_str}")
                        error_count += 1
                        continue

                    # Verificar o crear el SLC source (simplificado)
                    source = session.query(Product).filter(
                        Product.scene_id == source_scene_id
                    ).first()

                    if not source:
                        # Crear un SLC placeholder si no existe
                        source = Product(
                            scene_id=source_scene_id,
                            product_type="SLC",
                            acquisition_date=acquisition_date,
                            satellite_id="S1A",  # Placeholder
                            orbit_direction=orbit,
                            track_number=track,
                            subswath="IW",  # SLCs tienen subswath genérico
                            processing_status="DISCOVERED",
                        )
                        session.add(source)
                        session.flush()

                    # Registrar polarimetría
                    polar_product = api.register_polarimetry_product(
                        source_scene_id=source_scene_id,
                        decomposition_type="H-Alpha Dual Pol",
                        subswath=subswath,
                        orbit_direction=orbit,
                        track_number=track,
                    )

                    # Update status
                    polar_product.processing_status = "PROCESSED"

                    # Add storage location
                    api.add_storage_location(
                        product_id=polar_product.id,
                        storage_type="POLARIMETRY_PRODUCT",
                        file_path=file_path,
                        file_format="BEAM-DIMAP",
                        calculate_size=True,
                    )

                    migrated_count += 1
                else:
                    migrated_count += 1

                print()

            except Exception as e:
                print(f"❌ Error procesando {file_path}: {e}")
                error_count += 1
                print()

    if not dry_run:
        print(f"\n✅ {migrated_count} productos migrados")
    else:
        print(f"\n[DRY RUN] {migrated_count} productos se migrarían")

    if skipped_count > 0:
        print(f"⏭️  {skipped_count} productos ya existían en la BD")

    if error_count > 0:
        print(f"❌ {error_count} productos con errores")


def main():
    """Punto de entrada principal."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Migrar productos de polarimetría existentes a la BD'
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='Aplicar cambios (por defecto solo muestra lo que haría)'
    )
    parser.add_argument(
        '--base-dir',
        default='/mnt/satelit_data/processed_products',
        help='Directorio base de productos procesados'
    )

    args = parser.parse_args()

    print("=" * 80)
    print("MIGRATE POLARIMETRY PRODUCTS TO DATABASE")
    print("=" * 80)
    print()

    if args.apply:
        print("⚠️  MODO APLICAR - Los cambios se guardarán en la BD")
        response = input("¿Continuar? (yes/no): ")
        if response.lower() != 'yes':
            print("Cancelado")
            return
    else:
        print("ℹ️  MODO DRY RUN - Solo muestra los cambios (usa --apply para aplicar)")

    print()
    migrate_polarimetry_products(base_dir=args.base_dir, dry_run=not args.apply)


if __name__ == '__main__':
    main()
