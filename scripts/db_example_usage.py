#!/usr/bin/env python3
"""
Ejemplos de uso de la integración con satelit_metadata database.

Este script muestra cómo usar las funcionalidades de trazabilidad de productos
sin necesidad de modificar código existente.

Author: goshawk_ETL + satelit_metadata integration
"""

import sys
from pathlib import Path

# Add scripts to path
script_dir = Path(__file__).parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

from db_integration import get_db_integration

def example_1_check_slc_downloaded():
    """Ejemplo 1: Verificar si un SLC ya está descargado"""
    print("\n" + "="*80)
    print("EJEMPLO 1: Verificar si SLC está descargado")
    print("="*80)

    db = get_db_integration()

    scene_id = "S1A_IW_SLC__1SDV_20230111T060136_20230111T060203_046714_059C5B_F5B0"

    is_downloaded = db.is_slc_downloaded(scene_id)

    if is_downloaded:
        print(f"✅ {scene_id} ya está descargado (registrado en BD)")
    else:
        print(f"❌ {scene_id} NO está descargado")


def example_2_track_statistics():
    """Ejemplo 2: Obtener estadísticas de un track"""
    print("\n" + "="*80)
    print("EJEMPLO 2: Estadísticas de track")
    print("="*80)

    db = get_db_integration()

    orbit_direction = "DESCENDING"
    subswath = "IW1"
    track_number = 88

    stats = db.get_track_statistics(orbit_direction, subswath, track_number)

    if stats:
        print(f"\nTrack: {stats['track_id']}")
        print(f"Total SLCs: {stats['total_slc']}")
        print(f"Processed SLCs: {stats['processed_slc']}")
        print(f"InSAR short pairs: {stats['total_insar_short']}")
        print(f"InSAR long pairs: {stats['total_insar_long']}")
        print(f"Polarimetry products: {stats['total_polarimetry']}")
        print(f"Total size: {stats['total_size_gb']:.2f} GB")

        if stats['date_range']['first'] and stats['date_range']['last']:
            print(f"Date range: {stats['date_range']['first']} → {stats['date_range']['last']}")
    else:
        print("No statistics available (database not configured or empty)")


def example_3_check_can_delete():
    """Ejemplo 3: Verificar si un SLC puede borrarse"""
    print("\n" + "="*80)
    print("EJEMPLO 3: Verificar si SLC puede borrarse")
    print("="*80)

    db = get_db_integration()

    # Path ejemplo - ajustar a tu caso
    slc_path = "/mnt/satelit_data/sentinel1_slc/S1A_IW_SLC__1SDV_20230111T060136_20230111T060203_046714_059C5B_F5B0.SAFE"

    can_delete, reason = db.can_delete_slc(slc_path)

    if can_delete:
        print(f"✅ Puede borrarse: {reason}")
    else:
        print(f"❌ NO puede borrarse: {reason}")


def example_4_find_deletable_slcs():
    """Ejemplo 4: Encontrar todos los SLCs que pueden borrarse"""
    print("\n" + "="*80)
    print("EJEMPLO 4: Encontrar SLCs que pueden borrarse")
    print("="*80)

    # Este ejemplo requiere acceso directo a la API
    try:
        from satelit_db.database import get_session
        from satelit_db.api import SatelitDBAPI

        with get_session() as session:
            api = SatelitDBAPI(session)

            # Buscar SLCs procesados de un track específico
            slcs = api.find_products_by_criteria(
                product_type="SLC",
                orbit_direction="DESCENDING",
                subswath="IW1",
                track_number=88,
                processing_status="PROCESSED",
            )

            print(f"\nTotal SLCs procesados: {len(slcs)}")

            deletable_count = 0
            total_size_gb = 0

            for slc in slcs:
                can_delete, reason = api.can_delete_slc(slc.id)
                if can_delete:
                    # Calculate size
                    size_gb = sum(
                        loc.file_size_gb or 0
                        for loc in slc.storage_locations
                        if loc.storage_type == "ORIGINAL_SLC"
                    )

                    deletable_count += 1
                    total_size_gb += size_gb

                    print(f"  ✓ {slc.scene_id[:40]}... ({size_gb:.2f} GB)")

            print(f"\nTotal deletable: {deletable_count}")
            print(f"Total recoverable space: {total_size_gb:.2f} GB")

    except ImportError:
        print("❌ satelit_db not installed - install with:")
        print("   conda env update -f environment.yml")


def example_5_query_products():
    """Ejemplo 5: Consultas avanzadas de productos"""
    print("\n" + "="*80)
    print("EJEMPLO 5: Consultas avanzadas")
    print("="*80)

    try:
        from satelit_db.database import get_session
        from satelit_db.api import SatelitDBAPI
        from datetime import datetime

        with get_session() as session:
            api = SatelitDBAPI(session)

            # Consulta 1: Productos por fecha
            print("\n1. SLCs descargados en enero 2023:")
            products = api.find_products_by_criteria(
                product_type="SLC",
                start_date=datetime(2023, 1, 1),
                end_date=datetime(2023, 1, 31),
            )
            print(f"   Encontrados: {len(products)}")

            # Consulta 2: Productos que cubren un AOI
            print("\n2. Productos que cubren AOI (bbox ejemplo):")
            products = api.find_products_by_criteria(
                product_type="SLC",
                bbox=(2.49, 41.58, 2.57, 41.64),  # min_lon, min_lat, max_lon, max_lat
            )
            print(f"   Encontrados: {len(products)}")

            # Consulta 3: InSAR productos con buena coherencia
            print("\n3. InSAR productos con coherencia > 0.7:")
            from satelit_db.models import Product

            insar_good_coherence = (
                session.query(Product)
                .filter(
                    Product.product_type.in_(["INSAR_SHORT", "INSAR_LONG"]),
                    Product.coherence_mean > 0.7,
                )
                .all()
            )
            print(f"   Encontrados: {len(insar_good_coherence)}")

    except ImportError:
        print("❌ satelit_db not installed")


def example_6_cli_usage():
    """Ejemplo 6: Uso del CLI satelit-db"""
    print("\n" + "="*80)
    print("EJEMPLO 6: Comandos CLI disponibles")
    print("="*80)

    print("""
Los siguientes comandos están disponibles desde la terminal:

1. Ver estadísticas generales:
   $ satelit-db stats

2. Listar productos:
   $ satelit-db list-products --type SLC --track 88 --subswath IW1

3. Estadísticas de track:
   $ satelit-db track-stats --orbit DESCENDING --subswath IW1 --track 88

4. Ver SLCs que pueden borrarse:
   $ satelit-db deletable-slcs --track 88 --subswath IW1

5. Verificar SLC específico:
   $ satelit-db can-delete 12345  # 12345 = product ID

6. Ver cola de descargas:
   $ satelit-db list-downloads --status PENDING

7. Ver procesamiento reciente:
   $ satelit-db recent-processing --limit 20

NOTA: Requiere que satelit_metadata/docker compose esté corriendo
    """)


def main():
    """Run all examples"""
    print("\n" + "="*80)
    print("EJEMPLOS DE USO - satelit_metadata Database Integration")
    print("="*80)

    # Check if database is available
    db = get_db_integration()
    if not db.enabled:
        print("\n⚠️  WARNING: Database integration is not available")
        print("\nTo enable database integration:")
        print("1. cd ../satelit_metadata")
        print("2. make setup")
        print("3. conda env update -f environment.yml  # (in goshawk_ETL)")
        print("\n" + "="*80)
        return

    print("\n✅ Database integration is ENABLED\n")

    # Run examples
    try:
        example_1_check_slc_downloaded()
        example_2_track_statistics()
        example_3_check_can_delete()
        example_4_find_deletable_slcs()
        example_5_query_products()
        example_6_cli_usage()
    except Exception as e:
        print(f"\n❌ Error running examples: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "="*80)
    print("Ejemplos completados")
    print("="*80 + "\n")


if __name__ == "__main__":
    main()
