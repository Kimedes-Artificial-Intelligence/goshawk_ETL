#!/usr/bin/env python3
"""
Verificador de Disponibilidad Sentinel-1 - Pre-vuelo

Este script verifica la disponibilidad de órbitas y datos Sentinel-1 antes de
realizar descargas pesadas. Permite seleccionar el mejor satélite para trabajar.

Autor: SAR-based water leak detection project
Versión: 1.0
Fecha: 2025-11-15
"""

import os
import sys
import argparse
import requests
import re
from datetime import datetime, timedelta
from typing import Dict
from collections import defaultdict
from workflow_state import WorkflowState
# importar las credenciales desde un archivo .env externo si existe
from dotenv import load_dotenv
load_dotenv()

# ============================================================================
# CONFIGURACIÓN
# ============================================================================

# URLs
ESA_ORBITS_URL = 'https://step.esa.int/auxdata/orbits/Sentinel-1/{product}/{satellite}/{year}/{month:02}'
CATALOGUE_API = "https://catalogue.dataspace.copernicus.eu/odata/v1"
AUTH_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"

HTTP_TIMEOUT = 30

# AOI por defecto (Arenys de Munt) - coincide con aoi_arenys.geojson
DEFAULT_AOI = {
    'min_lon': 2.405319,
    'min_lat': 41.514032,
    'max_lon': 2.555008,
    'max_lat': 41.627029
}

# ============================================================================
# VERIFICACIÓN DE ÓRBITAS
# ============================================================================

def check_orbits_availability(
    satellite: str,
    product: str,
    start_date: datetime,
    end_date: datetime
) -> Dict:
    """
    Verifica disponibilidad de órbitas para un satélite y periodo

    Returns:
        Dict con estadísticas de disponibilidad
    """
    print(f"\n🛰️  Verificando órbitas {product} para {satellite}...")

    # Determinar meses a verificar
    months_to_check = set()
    current = start_date.replace(day=1)
    end_month = end_date.replace(day=1)

    while current <= end_month:
        months_to_check.add((current.year, current.month))
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)

    total_orbits = 0
    months_with_data = 0
    months_checked = len(months_to_check)
    orbit_dates = []

    for year, month in sorted(months_to_check):
        url = ESA_ORBITS_URL.format(
            product=product,
            satellite=satellite,
            year=year,
            month=month
        )

        try:
            response = requests.get(url, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            lines = response.text.splitlines()

            # Parsear HTML para contar órbitas
            pattern = r'<a href="(S1\w_OPER_AUX_(POE|RES)ORB_OPOD_\d{8}T\d{6}_V\d{8}T\d{6}_\d{8}T\d{6}.EOF.zip)">'

            month_orbits = 0
            for line in lines:
                match = re.search(pattern, line)
                if match:
                    month_orbits += 1
                    # Extraer fecha de la órbita
                    filename = match.group(1)
                    parts = filename.split('_')
                    date_str = parts[6][1:9]  # Remover 'V' y tomar YYYYMMDD
                    orbit_dates.append(date_str)

            if month_orbits > 0:
                months_with_data += 1
                total_orbits += month_orbits
                print(f"   {year}-{month:02d}: {month_orbits} órbitas")

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                print(f"   {year}-{month:02d}: No disponible")
            else:
                print(f"   {year}-{month:02d}: Error HTTP {e.response.status_code}")
        except Exception as e:
            print(f"   {year}-{month:02d}: Error - {e}")

    coverage = (months_with_data / months_checked * 100) if months_checked > 0 else 0

    result = {
        'satellite': satellite,
        'product': product,
        'total_orbits': total_orbits,
        'months_checked': months_checked,
        'months_with_data': months_with_data,
        'coverage_percent': coverage,
        'orbit_dates': sorted(list(set(orbit_dates)))
    }

    print(f"   ✅ Total: {total_orbits} órbitas en {months_with_data}/{months_checked} meses ({coverage:.1f}% cobertura)")

    return result

# ============================================================================
# VERIFICACIÓN DE IMÁGENES
# ============================================================================

def create_wkt_polygon(bbox: Dict) -> str:
    """Crea un polígono WKT desde un bounding box"""
    return (
        f"POLYGON(("
        f"{bbox['min_lon']} {bbox['min_lat']},"
        f"{bbox['max_lon']} {bbox['min_lat']},"
        f"{bbox['max_lon']} {bbox['max_lat']},"
        f"{bbox['min_lon']} {bbox['max_lat']},"
        f"{bbox['min_lon']} {bbox['min_lat']}"
        f"))"
    )

def check_images_availability(
    satellite: str,
    product_type: str,
    start_date: datetime,
    end_date: datetime,
    bbox: Dict,
    username: str,
    password: str
) -> Dict:
    """
    Verifica disponibilidad de imágenes Sentinel-1

    Returns:
        Dict con estadísticas de disponibilidad
    """
    print(f"\n📡 Verificando imágenes {product_type} para {satellite}...")

    # Autenticar
    data = {
        "client_id": "cdse-public",
        "username": username,
        "password": password,
        "grant_type": "password",
    }

    try:
        response = requests.post(AUTH_URL, data=data, timeout=30)
        response.raise_for_status()
        token = response.json()["access_token"]
    except Exception as e:
        print(f"   ❌ Error de autenticación: {e}")
        return {
            'satellite': satellite,
            'product_type': product_type,
            'total_images': 0,
            'error': str(e)
        }

    # Construir filtro
    start_str = start_date.strftime('%Y-%m-%dT00:00:00.000Z')
    end_str = end_date.strftime('%Y-%m-%dT23:59:59.999Z')
    wkt = create_wkt_polygon(bbox)

    filter_query = (
        f"Collection/Name eq 'SENTINEL-1' and "
        f"contains(Name,'{satellite}') and "
        f"contains(Name,'IW') and "
        f"contains(Name,'{product_type}') and "
        f"OData.CSC.Intersects(area=geography'SRID=4326;{wkt}') and "
        f"ContentDate/Start gt {start_str} and "
        f"ContentDate/Start lt {end_str} and "
        f"Attributes/OData.CSC.StringAttribute/any("
        f"att:att/Name eq 'orbitDirection' and "
        f"att/OData.CSC.StringAttribute/Value eq 'DESCENDING')"
    )

    url = f"{CATALOGUE_API}/Products"
    params = {
        "$filter": filter_query,
        "$top": 1000,
        "$orderby": "ContentDate/Start desc"
    }
    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = requests.get(url, params=params, headers=headers, timeout=60)
        response.raise_for_status()
        products = response.json().get('value', [])

        # Calcular estadísticas
        total_size = sum(p.get('ContentLength', 0) for p in products) / (1024**3)  # GB

        # Agrupar por mes
        by_month = defaultdict(int)
        for p in products:
            name = p.get('Name', '')
            if len(name) > 20:
                date_str = name.split('_')[4][:6]  # YYYYMM
                by_month[date_str] += 1

        print(f"   ✅ Total: {len(products)} imágenes (~{total_size:.1f} GB)")

        if by_month:
            print(f"   📅 Distribución por mes:")
            for month, count in sorted(by_month.items()):
                year, mon = month[:4], month[4:6]
                print(f"      {year}-{mon}: {count} imágenes")

        return {
            'satellite': satellite,
            'product_type': product_type,
            'total_images': len(products),
            'total_size_gb': total_size,
            'by_month': dict(by_month),
            'products': products[:5]  # Solo primeras 5 para referencia
        }

    except Exception as e:
        print(f"   ❌ Error: {e}")
        return {
            'satellite': satellite,
            'product_type': product_type,
            'total_images': 0,
            'error': str(e)
        }

# ============================================================================
# COMPARACIÓN Y RECOMENDACIÓN
# ============================================================================

def compare_satellites(
    orbits_results: Dict[str, Dict],
    images_results: Dict[str, Dict]
) -> str:
    """
    Compara disponibilidad entre satélites y recomienda el mejor

    Returns:
        Nombre del satélite recomendado
    """
    print("\n" + "="*80)
    print("📊 COMPARACIÓN DE DISPONIBILIDAD")
    print("="*80)

    scores = {}

    for sat in ['S1A', 'S1C']:
        if sat not in orbits_results or sat not in images_results:
            continue

        orbits = orbits_results[sat]
        images = images_results[sat]

        # Calcular score (0-100)
        # 40% cobertura de órbitas, 40% número de imágenes, 20% distribución temporal
        orbit_score = orbits.get('coverage_percent', 0) * 0.4
        image_score = min(images.get('total_images', 0) / 50.0 * 100, 100) * 0.4
        months_score = len(images.get('by_month', {})) / 6.0 * 100 * 0.2

        total_score = orbit_score + image_score + months_score
        scores[sat] = total_score

        print(f"\n{sat}:")
        print(f"  Órbitas: {orbits.get('total_orbits', 0)} disponibles ({orbits.get('coverage_percent', 0):.1f}% cobertura)")
        print(f"  Imágenes: {images.get('total_images', 0)} disponibles (~{images.get('total_size_gb', 0):.1f} GB)")
        print(f"  Meses con datos: {len(images.get('by_month', {}))}")
        print(f"  Score total: {total_score:.1f}/100")

    if not scores:
        return None

    best_sat = max(scores.keys(), key=lambda k: scores[k])

    # Advertencia especial si recomienda S1C
    if best_sat == 'S1C' and scores.get(best_sat, 0) == 0:
        print("\n" + "="*80)
        print(f"⚠️  ADVERTENCIA: S1C no está operativo")
        print("="*80)
        print(f"Sentinel-1C fue lanzado en 2024 pero aún no proporciona datos públicos")
        print(f"en Copernicus Dataspace Ecosystem.")
        print(f"\n🔄 Cambiando recomendación a S1A (operativo desde 2014)")

        # Cambiar a S1A si existe
        if 'S1A' in scores:
            best_sat = 'S1A'
        else:
            print(f"\n❌ No hay satélites operativos con datos disponibles")
            return None

    print("\n" + "="*80)
    print(f"🏆 RECOMENDACIÓN: {best_sat}")
    print("="*80)
    print(f"Score: {scores[best_sat]:.1f}/100")

    if scores[best_sat] < 50:
        print("⚠️  ADVERTENCIA: Baja disponibilidad de datos para este periodo")
        print("   Considera ampliar el rango temporal o verificar el área de interés")

    return best_sat

# ============================================================================
# MODO INTERACTIVO
# ============================================================================

def interactive_mode(username: str, password: str):
    """Modo interactivo de verificación"""
    print("\n" + "="*80)
    print("🔍 VERIFICADOR DE DISPONIBILIDAD SENTINEL-1")
    print("="*80)

    # Verificar si existe configuración previa
    state = WorkflowState()
    existing_config = state.load_workflow_state()

    if existing_config:
        print("\n" + "="*80)
        print("⚠️  CONFIGURACIÓN EXISTENTE DETECTADA")
        print("="*80)
        print(f"Satélite: {existing_config.get('satellite', 'N/A')}")
        print(f"Período: {existing_config.get('start_date', 'N/A')} → {existing_config.get('end_date', 'N/A')}")

        product_types = existing_config.get('product_types', [existing_config.get('product_type', 'N/A')])
        if isinstance(product_types, list):
            print(f"Productos: {', '.join(product_types)}")
        else:
            print(f"Productos: {product_types}")

        print(f"Paso actual: {existing_config.get('workflow_step', 0)}/4")
        print(f"Creado: {existing_config.get('created', 'N/A')}")

        print("\n" + "="*80)
        print("Para crear una nueva configuración, primero debes eliminar la existente.")
        print("="*80)

        delete_choice = input("\n¿Eliminar configuración existente y crear una nueva? (y/N): ").strip().lower()

        if delete_choice == 'y':
            state.reset_workflow_state()
            print("✅ Configuración anterior eliminada. Continuando con nueva configuración...\n")
        else:
            print("\n❌ Operación cancelada. Mantiene la configuración existente.")
            print("\nPara ver la configuración actual:")
            print("  python3 workflow_state.py show")
            print("\nPara continuar con el workflow existente:")
            print("  python3 download_orbits.py")
            return

    # Seleccionar tipo de producto
    print("\n📡 Tipo de producto:")
    print("1. SLC (InSAR, coherencia)")
    print("2. GRD (backscatter, texturas)")
    print("3. SLC + GRD (Fusión multi-producto)")
    prod_choice = input("\nOpción (1-3): ").strip()

    if prod_choice == '3':
        product_types = ["SLC", "GRD"]
        print("   ✅ Modo fusión: Se verificarán SLC y GRD para el mismo satélite")
    elif prod_choice == '2':
        product_types = ["GRD"]
    else:
        product_types = ["SLC"]

    # Seleccionar periodo
    print("\n📅 Periodo a verificar:")
    print("1. Último mes")
    print("2. Últimos 2 meses")
    print("3. Últimos 3 meses")
    print("4. Personalizado")

    date_choice = input("\nOpción (1-4): ").strip()

    end_date = datetime.now()

    if date_choice == '1':
        start_date = end_date - timedelta(days=30)
    elif date_choice == '2':
        start_date = end_date - timedelta(days=60)
    elif date_choice == '3':
        start_date = end_date - timedelta(days=90)
    else:
        start_str = input("Fecha inicio (YYYY-MM-DD): ").strip()
        end_str = input("Fecha fin (YYYY-MM-DD, Enter=hoy): ").strip()

        try:
            start_date = datetime.strptime(start_str, '%Y-%m-%d')
            if end_str:
                end_date = datetime.strptime(end_str, '%Y-%m-%d')
        except ValueError:
            print("⚠️  Formato inválido, usando último mes")
            start_date = end_date - timedelta(days=30)

    print(f"\n✅ Verificando: {start_date.date()} → {end_date.date()}")

    # Tipo de órbita
    print("\n🛰️  Tipo de órbita:")
    print("1. POEORB (Precisas, recomendado)")
    print("2. RESORB (Rápidas)")
    orbit_choice = input("\nOpción (1-2, default=1): ").strip()
    orbit_product = "POEORB" if orbit_choice != '2' else "RESORB"

    # Verificar órbitas para S1A y S1C
    print("\n" + "="*80)
    print("VERIFICANDO ÓRBITAS")
    print("="*80)

    orbits_results = {}
    for sat in ['S1A', 'S1C']:
        result = check_orbits_availability(sat, orbit_product, start_date, end_date)
        orbits_results[sat] = result

    # Verificar imágenes para S1A y S1C - para todos los productos seleccionados
    print("\n" + "="*80)
    print("VERIFICANDO IMÁGENES")
    print("="*80)

    # Diccionario para almacenar resultados por satélite y producto
    images_results = {sat: {} for sat in ['S1A', 'S1C']}

    for sat in ['S1A', 'S1C']:
        for product_type in product_types:
            result = check_images_availability(
                sat, product_type, start_date, end_date,
                DEFAULT_AOI, username, password
            )
            images_results[sat][product_type] = result

    # Comparar y recomendar (usando el primer producto para la comparación)
    # Crear formato compatible con compare_satellites
    images_results_for_comparison = {
        sat: images_results[sat][product_types[0]]
        for sat in ['S1A', 'S1C']
    }

    recommended = compare_satellites(orbits_results, images_results_for_comparison)

    if not recommended:
        print("\n❌ No se pudo determinar un satélite recomendado")
        return

    # Mostrar resumen multi-producto si aplica
    if len(product_types) > 1:
        print("\n" + "="*80)
        print(f"📊 RESUMEN MULTI-PRODUCTO PARA {recommended}")
        print("="*80)
        for product_type in product_types:
            result = images_results[recommended][product_type]
            print(f"\n{product_type}:")
            print(f"  Imágenes: {result.get('total_images', 0)}")
            print(f"  Tamaño: ~{result.get('total_size_gb', 0):.1f} GB")
            print(f"  Meses con datos: {len(result.get('by_month', {}))}")

    # Preguntar si guardar configuración para workflow
    print("\n" + "="*80)
    print("💾 GUARDAR CONFIGURACIÓN DE WORKFLOW")
    print("="*80)
    print("\nEsto creará un archivo .project_config.json que permitirá que")
    print("los siguientes scripts usen automáticamente esta configuración:")
    print(f"  - Satélite: {recommended}")
    print(f"  - Período: {start_date.date()} → {end_date.date()}")
    print(f"  - Productos: {', '.join(product_types)}")
    print(f"  - Órbitas: {orbit_product}")

    if len(product_types) > 1:
        print(f"\n💡 Configuración multi-producto detectada:")
        print(f"   Los scripts descargarán y procesarán {' y '.join(product_types)}")
        print(f"   para permitir fusión de datos posteriores.")

    save_choice = input("\n¿Guardar configuración? (Y/n): ").strip().lower()

    if save_choice != 'n':
        state = WorkflowState()
        state.save_workflow_state(
            satellite=recommended,
            start_date=start_date.strftime('%Y-%m-%d'),
            end_date=end_date.strftime('%Y-%m-%d'),
            product_types=product_types,  # Usar product_types en lugar de product_type
            step=1,  # Paso 1 completado (verificación)
            aoi={
                'lat_min': DEFAULT_AOI['min_lat'],
                'lat_max': DEFAULT_AOI['max_lat'],
                'lon_min': DEFAULT_AOI['min_lon'],
                'lon_max': DEFAULT_AOI['max_lon']
            },
            metadata={'orbit_product': orbit_product}
        )
        print("\n✅ ¡Configuración guardada!")
        print("   Los siguientes pasos usarán automáticamente esta configuración.\n")

    # Preguntar si proceder
    print(f"\n¿Deseas proceder a descargar órbitas para {recommended}?")
    choice = input("(Y/n): ").strip().lower()

    if choice != 'n':
        print(f"\n💡 Ejecuta el siguiente comando:")
        print(f"\npython3 download_orbits.py")
        print("# (Usará automáticamente la configuración guardada)")

        print(f"\nY luego descarga las imágenes con:")
        print(f"\npython3 download_copernicus.py --interactive")
        print(f"# (También usará la configuración guardada)")

        if len(product_types) > 1:
            print(f"\n📌 IMPORTANTE: Para workflow de fusión, deberás:")
            print(f"   1. Descargar los datos de ambos productos ({' y '.join(product_types)})")
            print(f"   2. Procesarlos por separado")
            print(f"   3. Ejecutar la fusión al final")

# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Verificador de disponibilidad Sentinel-1',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:

  # Modo interactivo (recomendado)
  python3 check_availability.py --interactive

  # Verificar órbitas solo
  python3 check_availability.py --check orbits --satellite S1A S1C --start-date 2024-01-01

  # Verificar todo
  python3 check_availability.py --check all --start-date 2024-01-01 --end-date 2024-06-01
        """
    )

    parser.add_argument('--interactive', '-i', action='store_true',
                       help='Modo interactivo')
    parser.add_argument('--check', choices=['orbits', 'images', 'all'],
                       default='all', help='Qué verificar')
    parser.add_argument('--satellite', nargs='+',
                       choices=['S1A', 'S1C'], default=['S1A', 'S1C'],
                       help='Satélites a verificar')
    parser.add_argument('--product-type', choices=['SLC', 'GRD'],
                       default='SLC', help='Tipo de producto')
    parser.add_argument('--orbit-product', choices=['POEORB', 'RESORB'],
                       default='POEORB', help='Tipo de órbita')
    parser.add_argument('--start-date', help='Fecha inicio (YYYY-MM-DD)')
    parser.add_argument('--end-date', help='Fecha fin (YYYY-MM-DD)')

    args = parser.parse_args()

    # Banner
    print("="*80)
    print("🔍 VERIFICADOR DE DISPONIBILIDAD SENTINEL-1")
    print("="*80)

    # Verificar si existe configuración previa (solo si no es modo interactivo)
    if not args.interactive:
        state = WorkflowState()
        existing_config = state.load_workflow_state()

        if existing_config:
            print("\n" + "="*80)
            print("⚠️  CONFIGURACIÓN EXISTENTE DETECTADA")
            print("="*80)
            print(f"Satélite: {existing_config.get('satellite', 'N/A')}")
            print(f"Período: {existing_config.get('start_date', 'N/A')} → {existing_config.get('end_date', 'N/A')}")

            product_types = existing_config.get('product_types', [existing_config.get('product_type', 'N/A')])
            if isinstance(product_types, list):
                print(f"Productos: {', '.join(product_types)}")
            else:
                print(f"Productos: {product_types}")

            print(f"Paso actual: {existing_config.get('workflow_step', 0)}/4")

            print("\n" + "="*80)
            print("Para crear una nueva configuración, primero debes eliminar la existente.")
            print("="*80)

            delete_choice = input("\n¿Eliminar configuración existente y crear una nueva? (y/N): ").strip().lower()

            if delete_choice == 'y':
                state.reset_workflow_state()
                print("✅ Configuración anterior eliminada. Continuando...\n")
            else:
                print("\n❌ Operación cancelada. Mantiene la configuración existente.")
                print("\nPara ver la configuración actual:")
                print("  python3 workflow_state.py show")
                print("\nPara eliminar la configuración:")
                print("  python3 workflow_state.py reset")
                return 1

    # Obtener credenciales si se verifican imágenes
    username = None
    password = None

    if args.check in ['images', 'all'] or args.interactive:
        username = os.environ.get('COPERNICUS_USER')
        password = os.environ.get('COPERNICUS_PASSWORD')

        if not username or not password:
            print("\n🔐 Credenciales requeridas para verificar imágenes")
            username = input("Usuario Copernicus: ")
            if not username:
                print("❌ Usuario requerido")
                return 1

            import getpass
            password = getpass.getpass("Contraseña: ")
            if not password:
                print("❌ Contraseña requerida")
                return 1

    # Modo interactivo
    if args.interactive:
        interactive_mode(username, password)
        return 0

    # Parsear fechas
    end_date = datetime.now()
    if args.end_date:
        try:
            end_date = datetime.strptime(args.end_date, '%Y-%m-%d')
        except ValueError:
            print(f"❌ Fecha inválida: {args.end_date}")
            return 1

    start_date = end_date - timedelta(days=180)
    if args.start_date:
        try:
            start_date = datetime.strptime(args.start_date, '%Y-%m-%d')
        except ValueError:
            print(f"❌ Fecha inválida: {args.start_date}")
            return 1

    print(f"\n📅 Periodo: {start_date.date()} → {end_date.date()}")

    # Verificar órbitas
    orbits_results = {}
    if args.check in ['orbits', 'all']:
        print("\n" + "="*80)
        print("VERIFICANDO ÓRBITAS")
        print("="*80)
        for sat in args.satellite:
            result = check_orbits_availability(sat, args.orbit_product, start_date, end_date)
            orbits_results[sat] = result

    # Verificar imágenes
    images_results = {}
    if args.check in ['images', 'all']:
        print("\n" + "="*80)
        print("VERIFICANDO IMÁGENES")
        print("="*80)
        for sat in args.satellite:
            result = check_images_availability(
                sat, args.product_type, start_date, end_date,
                DEFAULT_AOI, username, password
            )
            images_results[sat] = result

    # Comparar si verificamos todo
    if args.check == 'all' and len(args.satellite) > 1:
        recommended = compare_satellites(orbits_results, images_results)

        if recommended:
            # Ofrecer guardar configuración
            print("\n💾 ¿Guardar esta configuración para el workflow? (Y/n): ", end='')
            save_choice = input().strip().lower()

            if save_choice != 'n':
                state = WorkflowState()
                state.save_workflow_state(
                    satellite=recommended,
                    start_date=start_date.strftime('%Y-%m-%d'),
                    end_date=end_date.strftime('%Y-%m-%d'),
                    product_type=args.product_type,
                    step=1,
                    aoi={
                        'lat_min': DEFAULT_AOI['min_lat'],
                        'lat_max': DEFAULT_AOI['max_lat'],
                        'lon_min': DEFAULT_AOI['min_lon'],
                        'lon_max': DEFAULT_AOI['max_lon']
                    },
                    metadata={'orbit_product': args.orbit_product}
                )

    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrumpido")
        sys.exit(130)
