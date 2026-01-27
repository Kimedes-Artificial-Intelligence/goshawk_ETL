#!/usr/bin/env python3
"""
Script para corregir el subswath de productos InSAR y Polarimetría en la BD.

Los productos heredaban subswath='IW' del master SLC, pero deberían tener
el subswath específico (IW1, IW2, IW3) según el procesamiento.

Este script:
1. Encuentra todos los productos InSAR y Polarimetría con subswath='IW'
2. Extrae el subswath correcto de la ruta de almacenamiento
3. Actualiza el campo subswath en la BD

Author: goshawk_ETL
"""

import re
import sys
from pathlib import Path

# Add scripts to path
script_dir = Path(__file__).parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

from satelit_db.database import get_session
from satelit_db.api import SatelitDBAPI
from satelit_db.models import Product, StorageLocation


def extract_subswath_from_path(path: str) -> str:
    """
    Extrae el subswath de una ruta de producto.

    Ejemplos:
      /path/desc_iw1/t088/insar/... → IW1
      /path/asce_iw2/t037/insar/... → IW2
      /path/insar_desc_iw1/... → IW1
    """
    path_lower = path.lower()

    # Buscar patrones desc_iw1, asce_iw2, etc.
    match = re.search(r'_(iw[123])', path_lower)
    if match:
        return match.group(1).upper()

    # Buscar patrones /iw1/, /iw2/, etc.
    match = re.search(r'/(iw[123])/', path_lower)
    if match:
        return match.group(1).upper()

    return None


def fix_subswaths(dry_run: bool = True):
    """
    Corrige los subswaths de productos en la BD.

    Args:
        dry_run: Si True, solo muestra los cambios sin aplicarlos
    """
    with get_session() as session:
        api = SatelitDBAPI(session)

        # Buscar productos con subswath='IW' (genérico)
        products_to_fix = session.query(Product).filter(
            Product.subswath == 'IW',
            Product.product_type.in_(['INSAR_SHORT', 'INSAR_LONG', 'POLARIMETRY'])
        ).all()

        print(f"Productos encontrados con subswath='IW': {len(products_to_fix)}")

        if not products_to_fix:
            print("No hay productos para corregir")
            return

        fixed_count = 0
        error_count = 0

        for product in products_to_fix:
            # Buscar la ruta de almacenamiento
            storage = session.query(StorageLocation).filter(
                StorageLocation.product_id == product.id
            ).first()

            if not storage:
                print(f"❌ {product.scene_id}: Sin ruta de almacenamiento")
                error_count += 1
                continue

            # Extraer subswath de la ruta
            subswath = extract_subswath_from_path(storage.file_path)

            if not subswath:
                print(f"❌ {product.scene_id}: No se pudo extraer subswath de {storage.file_path}")
                error_count += 1
                continue

            # Mostrar cambio
            print(f"{'[DRY RUN] ' if dry_run else ''}✓ {product.scene_id[:50]}")
            print(f"  {product.product_type} | Track {product.track_number} | {product.orbit_direction[:4]}")
            print(f"  Subswath: {product.subswath} → {subswath}")
            print(f"  Path: {storage.file_path[:80]}...")

            if not dry_run:
                product.subswath = subswath
                fixed_count += 1
            else:
                fixed_count += 1

            print()

        if not dry_run:
            session.commit()
            print(f"\n✅ {fixed_count} productos actualizados")
        else:
            print(f"\n[DRY RUN] {fixed_count} productos se actualizarían")

        if error_count > 0:
            print(f"⚠️  {error_count} productos con errores")


def main():
    """Punto de entrada principal."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Corregir subswaths de productos InSAR/Polarimetría en la BD'
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='Aplicar cambios (por defecto solo muestra lo que haría)'
    )

    args = parser.parse_args()

    print("=" * 80)
    print("FIX SUBSWATH IN DATABASE")
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
    fix_subswaths(dry_run=not args.apply)


if __name__ == '__main__':
    main()
