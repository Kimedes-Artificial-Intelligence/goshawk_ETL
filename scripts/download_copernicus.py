#!/usr/bin/env python3
"""
Descargador de Imágenes de Satélite - Copernicus Dataspace Ecosystem
Script robusto y funcional para descargar productos Sentinel con modo interactivo

Autor: SAR-based water leak detection project
Versión: 2.0
Fecha: 2025-11-13
"""

import argparse
import glob
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
import requests
from shapely import wkt as shapely_wkt
from shapely.geometry import Polygon, box


# Add parent directory to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)
from aoi_utils import geojson_to_bbox
from logging_utils import LoggerConfig
from common_utils import get_snap_orbits_dir
from insar_repository import InSARRepository

# Database integration (optional - graceful degradation if not available)
# ISSUE #5: Updated to use new db_queries API from Issue #2
# ISSUE #6: Added S2 functions for Sentinel-2 tracking
try:
    from scripts.db_queries import (
        register_slc_download, get_slc_status,
        register_s2_download, get_s2_status
    )
    from scripts.db_integration import init_db
    DB_INTEGRATION_AVAILABLE = init_db()
except ImportError:
    DB_INTEGRATION_AVAILABLE = False
    # Define no-op functions for graceful degradation
    def register_slc_download(*args, **kwargs):
        return None
    def get_slc_status(*args, **kwargs):
        return None
    def register_s2_download(*args, **kwargs):
        return None
    def get_s2_status(*args, **kwargs):
        return None

# importar las credenciales desde un archivo .env externo si existe
from dotenv import load_dotenv
load_dotenv()


# Logger global - se configurará en main()
logger = None

# Esta variable global se actualiza al parsear argumentos
BBOX = None

# APIs de Copernicus Data Space Ecosystem
CATALOGUE_API = "https://catalogue.dataspace.copernicus.eu/odata/v1"
ZIPPER_API = "https://zipper.dataspace.copernicus.eu/odata/v1"  # ← CORRECTO para descargas
AUTH_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"

# Use absolute path for BASE_DIR to work correctly from any directory
BASE_DIR = os.path.join(parent_dir, "data")
ORBITS_DIR = get_snap_orbits_dir()


class CopernicusAuth:
    """Maneja autenticación OAuth2 con Copernicus Dataspace"""

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.token = None
        self.token_expiry = None

    def get_token(self, force_refresh: bool = False) -> Optional[str]:
        """Obtiene un token de acceso válido"""
        if not force_refresh and self.token and self.token_expiry:
            if datetime.now() < self.token_expiry:
                return self.token

        data = {
            "client_id": "cdse-public",
            "username": self.username,
            "password": self.password,
            "grant_type": "password",
        }

        try:
            response = requests.post(AUTH_URL, data=data, timeout=30)
            response.raise_for_status()

            token_data = response.json()
            self.token = token_data["access_token"]

            # Calcular expiración (con margen de 60s)
            expires_in = token_data.get("expires_in", 600)
            self.token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)

            return self.token

        except requests.exceptions.RequestException as e:
            logger.info(f"❌ Error de autenticación: {e}")
            if hasattr(e, 'response') and e.response:
                logger.info(f"   Respuesta: {e.response.text}")
            return None


def load_aoi_from_geojson(geojson_path: str) -> Optional[Dict]:
    """Carga AOI desde archivo GeoJSON y extrae nombre"""
    try:
        bbox = geojson_to_bbox(geojson_path)
        if not bbox:
            return None
        
        # Extraer nombre del AOI
        aoi_name = None
        try:
            with open(geojson_path, 'r') as f:
                geojson_data = json.load(f)
                # Intentar extraer nombre desde properties
                if isinstance(geojson_data, dict):
                    if 'features' in geojson_data and len(geojson_data['features']) > 0:
                        aoi_name = geojson_data['features'][0].get('properties', {}).get('name')
                    elif 'properties' in geojson_data:
                        aoi_name = geojson_data['properties'].get('name')
        except:
            pass
        
        # Si no se encontró nombre en el GeoJSON, usar nombre del archivo
        if not aoi_name:
            aoi_name = os.path.splitext(os.path.basename(geojson_path))[0]
            # Formatear: "arenys_de_munt" -> "Arenys de Munt"
            aoi_name = aoi_name.replace('_', ' ').title()
        
        bbox['aoi_name'] = aoi_name
        return bbox
        
    except Exception as e:
        logger.info(f"️  No se pudo cargar {geojson_path}: {e}")
        return None


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

def parse_product_name(name: str) -> Dict:
    """
    Parsea el nombre de un producto Sentinel para extraer información

    Ejemplos:
    - S1A_IW_SLC__1SDV_20250928T055322_20250928T055350_061184_07A103_4484.SAFE
    - S2A_MSIL2A_20251109T105311_N0511_R051_T31TDF_20251109T135748.SAFE
    """
    try:
        # Remover extensión .SAFE si existe
        clean_name = name.replace('.SAFE', '')
        parts = clean_name.split('_')

        if name.startswith('S1'):  # Sentinel-1
            # Formato: S1A_IW_SLC__1SDV_20250928T055322_20250928T055350_...
            # El doble __ crea un elemento vacío, así que la fecha está en parts[5]
            satellite = parts[0]  # S1A, S1B, S1C
            mode = parts[1]  # IW, EW, SM
            product_type = parts[2]  # SLC, GRD

            # Buscar el primer elemento que parece una fecha (formato: 20YYMMDDTHHMMSS)
            date_str = None
            for part in parts[4:7]:  # La fecha suele estar en posiciones 4-6
                if len(part) >= 15 and 'T' in part and part[:4].isdigit():
                    date_str = part[:15]  # Tomar solo los primeros 15 caracteres
                    break

            if not date_str:
                raise ValueError("No se encontró fecha en el nombre")

            date = datetime.strptime(date_str, '%Y%m%dT%H%M%S')

            return {
                'satellite': satellite,
                'mode': mode,
                'product_type': product_type,
                'date': date,
                'date_str': date.strftime('%Y-%m-%d'),
                'time_str': date.strftime('%H:%M:%S')
            }

        elif name.startswith('S2'):  # Sentinel-2
            # Formato: S2A_MSIL2A_20251109T105311_N0511_R051_T31TDF_...
            satellite = parts[0]  # S2A, S2B
            level = parts[1]  # MSIL1C, MSIL2A
            date_str = parts[2][:15]  # 20251109T105311
            date = datetime.strptime(date_str, '%Y%m%dT%H%M%S')

            return {
                'satellite': satellite,
                'product_type': level,
                'date': date,
                'date_str': date.strftime('%Y-%m-%d'),
                'time_str': date.strftime('%H:%M:%S')
            }
    except Exception as e:
        # Debug: descomentar para ver errores de parseo
        # logger.info(f"Error parseando '{name}': {e}")
        pass

    return {
        'satellite': 'N/A',
        'product_type': 'N/A',
        'date': None,
        'date_str': 'N/A',
        'time_str': 'N/A'
    }

def search_products(
    auth: CopernicusAuth,
    collection: str = "SENTINEL-1",
    product_type: str = "SLC",
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    bbox: Optional[Dict] = None,
    max_cloud_cover: int = 30,
    satellite: Optional[str] = None,
    orbit_direction: Optional[str] = None,
    aoi_name: Optional[str] = None
) -> Optional[List[Dict]]:
    """Busca productos en el catálogo de Copernicus"""

    logger.info("="*80)
    if satellite and collection == "SENTINEL-1":
        logger.info(f"BÚSQUEDA: {collection} {product_type} ({satellite})")
    else:
        logger.info(f"BÚSQUEDA: {collection} {product_type}")

    # Informar sobre exclusión de productos COG para GRD
    if collection == "SENTINEL-1" and product_type == "GRD":
        logger.info("💡 Excluyendo productos COG (no compatibles con SNAP)")

    logger.info("="*80)

    # Autenticación
    logger.info("Autenticando...")
    token = auth.get_token()
    if not token:
        return None
    logger.info("Autenticado")

    # bbox por defecto
    if bbox is None:
        bbox = BBOX

    # Fechas por defecto si no se especifican
    if not end_date:
        end_date = datetime.now()
    if not start_date:
        start_date = end_date - timedelta(days=180)  # 6 meses por defecto

    start_str = start_date.strftime('%Y-%m-%dT00:00:00.000Z')
    end_str = end_date.strftime('%Y-%m-%dT23:59:59.999Z')

    # Crear WKT polygon
    wkt = create_wkt_polygon(bbox)

    # Construir filtro según la colección
    if collection == "SENTINEL-1":
        filter_query = (
            f"Collection/Name eq 'SENTINEL-1' and "
            f"contains(Name,'IW') and "
            f"contains(Name,'{product_type}') and "
        )

        # Excluir productos COG (Cloud Optimized GeoTIFF) para GRD
        # Los productos COG no son compatibles con procesamiento SNAP
        if product_type == "GRD":
            filter_query += f"not contains(Name,'_COG') and "

        # Filtrar por satélite si se especifica
        if satellite:
            # Advertencia si es S1C (no operativo aún)
            if satellite == 'S1C':
                logger.info(f"   ADVERTENCIA: Sentinel-1C fue lanzado pero aún no está operativo")
                logger.info(f"   No hay datos públicos disponibles para S1C en Copernicus")
                logger.info(f"   Recomendación: Usa S1A (operativo desde 2014)")

            filter_query += f"contains(Name,'{satellite}') and "

        # Filtrar por dirección de órbita si se especifica
        if orbit_direction:
            filter_query += (
                f"Attributes/OData.CSC.StringAttribute/any("
                f"att:att/Name eq 'orbitDirection' and "
                f"att/OData.CSC.StringAttribute/Value eq '{orbit_direction}') and "
            )

        filter_query += (
            f"OData.CSC.Intersects(area=geography'SRID=4326;{wkt}') and "
            f"ContentDate/Start gt {start_str} and "
            f"ContentDate/Start lt {end_str}"
        )
    elif collection == "SENTINEL-2":
        filter_query = (
            f"Collection/Name eq 'SENTINEL-2' and "
            f"contains(Name,'{product_type}') and "
            f"OData.CSC.Intersects(area=geography'SRID=4326;{wkt}') and "
            f"ContentDate/Start gt {start_str} and "
            f"ContentDate/Start lt {end_str} and "
            f"Attributes/OData.CSC.DoubleAttribute/any("
            f"att:att/Name eq 'cloudCover' and "
            f"att/OData.CSC.DoubleAttribute/Value lt {max_cloud_cover})"
        )
    else:
        logger.info(f"❌ Colección no soportada: {collection}")
        return None

    logger.info(f"🔍 Buscando...")
    logger.info(f"   Fechas: {start_date.date()} → {end_date.date()}")
    if aoi_name:
        logger.info(f"   Área: {aoi_name}")
    else:
        logger.info(f"   Área: BBOX custom")
    if collection == "SENTINEL-1":
        orbit_info = orbit_direction if orbit_direction else "ASCENDING + DESCENDING"
        logger.info(f"   Modo: IW, Órbitas: {orbit_info}")
    else:
        logger.info(f"   Nubosidad: < {max_cloud_cover}%")

    # Realizar búsqueda
    url = f"{CATALOGUE_API}/Products"
    params = {
        "$filter": filter_query,
        "$top": 1000,
        "$orderby": "ContentDate/Start desc"
    }
    headers = {"Authorization": f"Bearer {token}"}

    # Sentinel-2 necesita más tiempo por mayor volumen de productos
    timeout = 180 if collection == "SENTINEL-2" else 60

    try:
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
        response.raise_for_status()

        products = response.json().get('value', [])

        if not products:
            logger.info("❌ No se encontraron productos")
            return None

        logger.info(f"✅ Encontrados {len(products)} productos")
        return products

    except requests.exceptions.RequestException as e:
        logger.info(f"❌ Error en búsqueda: {e}")
        return None


def calculate_product_coverage(product: Dict, aoi_bbox: Dict) -> Tuple[float, float]:
    """
    Calcula la cobertura de un producto sobre el AOI usando el footprint.
    
    Args:
        product: Producto de Copernicus con campo GeoFootprint o Footprint
        aoi_bbox: AOI como dict con min_lon, max_lon, min_lat, max_lat
    
    Returns:
        Tuple (coverage_percentage, intersection_area_deg2)
        - coverage_percentage: % del AOI cubierto por el producto (0-100)
        - intersection_area_deg2: Área de intersección en grados cuadrados
    """
    try:

        # Crear polígono del AOI
        aoi_polygon = box(
            aoi_bbox['min_lon'], 
            aoi_bbox['min_lat'], 
            aoi_bbox['max_lon'], 
            aoi_bbox['max_lat']
        )
        aoi_area = aoi_polygon.area
        
        # Extraer geometría del producto
        product_polygon = None
        
        # Opción 1: GeoFootprint (GeoJSON)
        if 'GeoFootprint' in product:
            geofootprint = product['GeoFootprint']
            if geofootprint and 'coordinates' in geofootprint:
                coords = geofootprint['coordinates']
                if coords:
                    product_polygon = Polygon(coords[0])
        
        # Opción 2: Footprint (WKT)
        elif 'Footprint' in product:
            footprint_str = product['Footprint']
            # Formato: "geography'SRID=4326;POLYGON((...))')"
            if 'POLYGON' in footprint_str:
                # Extraer solo el WKT
                wkt_start = footprint_str.find('POLYGON')
                wkt_end = footprint_str.rfind("'")
                if wkt_start != -1:
                    if wkt_end > wkt_start:
                        wkt_str = footprint_str[wkt_start:wkt_end]
                    else:
                        wkt_str = footprint_str[wkt_start:]
                    product_polygon = shapely_wkt.loads(wkt_str)
        
        if not product_polygon or not product_polygon.is_valid:
            return (0.0, 0.0)
        
        # Calcular intersección
        if not aoi_polygon.intersects(product_polygon):
            return (0.0, 0.0)
        
        intersection = aoi_polygon.intersection(product_polygon)
        intersection_area = intersection.area
        
        # Calcular porcentaje de cobertura
        coverage_pct = (intersection_area / aoi_area) * 100.0 if aoi_area > 0 else 0.0
        
        return (coverage_pct, intersection_area)
        
    except ImportError:
        # Shapely no disponible, no podemos calcular cobertura
        return (100.0, 0.0)  # Asumir cobertura completa
    except Exception as e:
        # Error en cálculo, asumir cobertura completa
        return (100.0, 0.0)


def filter_products_by_coverage(products: List[Dict], aoi_bbox: Dict, 
                                 min_coverage_pct: float = 10.0,
                                 verbose: bool = True) -> List[Dict]:
    """
    Filtra productos por cobertura mínima del AOI.
    
    Args:
        products: Lista de productos de Copernicus
        aoi_bbox: AOI como dict
        min_coverage_pct: Porcentaje mínimo de cobertura requerido (default: 10%)
        verbose: Mostrar información de filtrado
    
    Returns:
        Lista de productos que cubren >= min_coverage_pct del AOI
    """
    if not aoi_bbox:
        return products  # Sin AOI, no filtrar
    
    try:
        # Verificar que Shapely está disponible
        from shapely.geometry import Polygon
    except ImportError:
        if verbose:
            logger.info("  Shapely no disponible, no se puede filtrar por cobertura")
        return products
    
    filtered = []
    coverage_info = []
    
    if verbose:
        logger.info(f"   Analizando cobertura de productos sobre el AOI...")
        logger.info(f"   Umbral mínimo: {min_coverage_pct}%")
    
    for product in products:
        coverage_pct, intersection_area = calculate_product_coverage(product, aoi_bbox)
        
        coverage_info.append({
            'product': product,
            'coverage_pct': coverage_pct,
            'intersection_area': intersection_area
        })
        
        if coverage_pct >= min_coverage_pct:
            # Agregar info de cobertura al producto
            product['_aoi_coverage_pct'] = coverage_pct
            product['_aoi_intersection_area'] = intersection_area
            filtered.append(product)
    
    if verbose:
        logger.info(f"   ✅ {len(filtered)}/{len(products)} productos cubren >= {min_coverage_pct}% del AOI")
        
        # Mostrar productos descartados si hay
        discarded = len(products) - len(filtered)
        if discarded > 0:
            logger.info(f"{discarded} productos descartados por cobertura insuficiente:")
            for info in coverage_info:
                if info['coverage_pct'] < min_coverage_pct:
                    name = info['product'].get('Name', 'Unknown')[:70]
                    logger.info(f"      - {name}... ({info['coverage_pct']:.1f}%)")
    
    return filtered


def display_products(products: List[Dict], limit: int = 20) -> float:
    """Muestra productos en formato tabla"""
    if not products:
        return 0.0

    # Verificar si tenemos información de cobertura o estado de procesamiento
    has_coverage = any('_aoi_coverage_pct' in p for p in products)
    has_status = any('_is_processed' in p or '_is_downloaded' in p or '_is_needed' in p for p in products)
    
    logger.info("" + "-"*140)
    header = f"{'#':<4} {'Fecha':<12} {'Hora':<10} {'Satélite':<8} {'Tipo':<8} {'Tamaño (GB)':<12}"
    if has_coverage:
        header += f" {'Cobertura':<10}"
    if has_status:
        header += f" {'Estado':<20}"
    logger.info(header)
    logger.info("-"*140)

    total_size_gb = 0.0

    for idx, product in enumerate(products, 1):
        name = product.get('Name', 'N/A')
        size_bytes = product.get('ContentLength', 0)
        size_gb = size_bytes / (1024**3)
        total_size_gb += size_gb

        # Parsear nombre del producto
        info = parse_product_name(name)

        if idx <= limit:
            line = (f"{idx:<4} {info['date_str']:<12} {info['time_str']:<10} "
                   f"{info['satellite']:<8} {info['product_type']:<8} {size_gb:<12.2f}")
            
            if has_coverage and '_aoi_coverage_pct' in product:
                coverage = product['_aoi_coverage_pct']
                line += f" {coverage:>6.1f}%   "
            elif has_coverage:
                line += f" {'N/A':<10}"
            
            if has_status:
                status = "Nuevo"
                if product.get('_is_needed'):
                    status = "🎯 Necesario"
                elif product.get('_is_processed'):
                    status = "✓ Procesado"
                elif product.get('_is_downloaded'):
                    status = "✓ Descargado"
                line += f" {status:<20}"
            
            logger.info(line)

    if len(products) > limit:
        logger.info(f"... y {len(products) - limit} productos más")

    logger.info("-"*140)
    logger.info(f"Total: {len(products)} productos, ~{total_size_gb:.1f} GB")
    logger.info("-"*140)

    return total_size_gb


def _extract_zip_with_progress(zip_ref: zipfile.ZipFile, extract_path: str, namelist: list) -> None:
    """
    Extrae archivos de un ZIP de forma optimizada.
    
    Prioridad de métodos (del más rápido al más lento):
    1. Comando 'unzip' del sistema (~4x más rápido)
    2. extractall() de Python (~3x más rápido)
    3. extract() en loop (fallback con progreso)
    
    Args:
        zip_ref: ZipFile abierto
        extract_path: Directorio donde extraer
        namelist: Lista de nombres de archivos a extraer
    """

    total_files = len(namelist)
    total_size = sum(zip_ref.getinfo(name).file_size for name in namelist)
    
    logger.info(f"   ⏳ Extrayendo {total_files} archivos ({total_size / (1024**3):.2f} GB)...")
    
    # OPCIÓN 1: Usar extractall() de Python (RÁPIDO y confiable)
    # En pruebas, es más rápido y confiable que unzip en muchos sistemas
    try:
        zip_ref.extractall(extract_path)
        logger.info(f"   ✅ Extracción completada: {total_files}/{total_files} archivos")
        return
    except Exception as e:
        logger.info(f"extractall() falló: {e}, intentando con unzip...")
    
    # OPCIÓN 2: Intentar usar 'unzip' del sistema (alternativa)
    if shutil.which('unzip'):
        try:
            zip_path = zip_ref.filename
            # Estimar timeout basado en tamaño: ~2 min por GB + 5 min base
            estimated_timeout = int((total_size / (1024**3)) * 120) + 300  # segundos
            timeout = max(900, estimated_timeout)  # Mínimo 15 minutos
            
            # unzip -q (silencioso) -o (sobrescribir) archivo.zip -d destino/
            result = subprocess.run(
                ['unzip', '-q', '-o', zip_path, '-d', extract_path],
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            if result.returncode == 0:
                logger.info(f"   ✅ Extracción completada: {total_files}/{total_files} archivos")
                return
            else:
                # Si falla, continuar con método de fallback
                logger.info(f"unzip falló (código {result.returncode}), usando último recurso...")
        except subprocess.TimeoutExpired:
            logger.info(f"unzip demasiado lento (>{timeout//60} min), usando último recurso...")
        except Exception as e:
            logger.info(f"Error con unzip: {e}, usando último recurso...")
    
    # OPCIÓN 3: Fallback con progreso (LENTO pero confiable)
    extracted = 0
    last_progress = 0
    extracted_size = 0
    
    for name in namelist:
        try:
            zip_ref.extract(name, extract_path)
            
            extracted += 1
            file_info = zip_ref.getinfo(name)
            extracted_size += file_info.file_size

                
        except Exception as e:
            logger.info(f"Error extrayendo {name}: {e}")
            continue
    
    logger.info(f"Extracción completada: {extracted}/{total_files} archivos (método: extract)")


def download_product(
    product: Dict,
    auth: CopernicusAuth,
    download_dir: str
) -> bool:
    """
    Descarga un producto individual usando el Zipper API

    Añadido: soporte para reanudar descargas interrumpidas mediante el uso
    de cabeceras HTTP 'Range' cuando exista un archivo .zip parcial.
    """
    product_id = product['Id']
    product_name = product['Name']
    output_file = os.path.join(download_dir, f"{product_name}.zip")
    extracted_dir = os.path.join(download_dir, product_name)

    # ISSUE #6: CHECK DATABASE: Verificar si ya está registrado como descargado (S1 or S2)
    if DB_INTEGRATION_AVAILABLE:
        # Detect product type
        is_s2 = product_name.startswith('S2')
        
        if is_s2:
            status = get_s2_status(product_name)
        else:
            status = get_slc_status(product_name)
            
        if status and status.get('downloaded', False):
            # Verificar que el archivo local realmente existe
            if os.path.exists(extracted_dir):
                manifest_file = os.path.join(extracted_dir, 'manifest.safe')
                if os.path.exists(manifest_file):
                    logger.info(f"⏭️  Ya descargado (BD): {product_name}")
                    return True
            # Si no existe localmente pero está en BD, continuar con descarga
            logger.debug(f"DB shows downloaded but file missing: {product_name}")

    # Verificar si ya existe (directorio extraído o .zip)
    if os.path.exists(extracted_dir):
        # Verificar integridad: debe existir manifest.safe
        manifest_file = os.path.join(extracted_dir, 'manifest.safe')
        if os.path.exists(manifest_file):
            logger.info(f"Ya existe (completo): {product_name}")
            return True
        else:
            logger.info(f"Directorio incompleto: {product_name}")
            logger.info(f"Eliminando directorio corrupto...")
            try:
                shutil.rmtree(extracted_dir)
            except Exception:
                pass
            # Continuar con descarga

    # Si existe un .zip, intentaremos usarlo / reanudar
    resume_from = 0
    if os.path.exists(output_file):
        size_gb = os.path.getsize(output_file) / (1024**3)
        logger.info(f"Ya existe (.zip): {product_name} ({size_gb:.2f} GB)")

        # Intentar extraer si parece válido
        try:
            if zipfile.is_zipfile(output_file):
                with zipfile.ZipFile(output_file, 'r') as zip_ref:
                    namelist = zip_ref.namelist()
                    # Comprobar si contiene manifest.safe en raíz o subdir
                    if any('manifest.safe' in n for n in namelist):
                        logger.info(f"   ✅ Archivo .zip completo y contiene manifest.safe, extrayendo...")
                        _extract_zip_with_progress(zip_ref, download_dir, namelist)
                        manifest_file = os.path.join(extracted_dir, 'manifest.safe')
                        if os.path.exists(manifest_file):
                            try:
                                os.remove(output_file)
                            except Exception:
                                pass
                            logger.info(f"Archivo .zip eliminado")
                            return True
            # Si llegamos aquí, el zip existe pero no es completamente válido
            logger.info(f"Archivo .zip presente pero incompleto o no contiene manifest.safe: se intentará reanudar descarga")
            resume_from = os.path.getsize(output_file)
        except zipfile.BadZipFile:
            logger.info(f"Archivo .zip corrupto o incompleto: se intentará reanudar descarga")
            resume_from = os.path.getsize(output_file)
        except Exception as e:
            logger.info(f"Error comprobando .zip existente: {e}. Se intentará reanudar descarga")
            resume_from = os.path.getsize(output_file)

    logger.info(f"Descargando: {product_name}")

    # Obtener token fresco
    token = auth.get_token()
    if not token:
        logger.info("No se pudo obtener token")
        return False

    # USAR ZIPPER API (no catalogue API)
    download_url = f"{ZIPPER_API}/Products({product_id})/$value"

    # Retries y backoff
    MAX_RETRIES = 5
    backoff = 2

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}"
    })

    attempt = 0
    while attempt < MAX_RETRIES:
        attempt += 1
        try:
            headers = {}
            mode = 'wb'
            existing_size = 0

            if resume_from and os.path.exists(output_file):
                existing_size = resume_from
                headers['Range'] = f'bytes={existing_size}-'
                mode = 'ab'  # append
                logger.info(f"Reanudando desde byte: {existing_size}")

            logger.info("Conectando...")
            response = session.get(download_url, headers=headers, stream=True, timeout=300)

            # Si auth expiró
            if response.status_code == 401:
                logger.info("Renovando token...")
                token = auth.get_token(force_refresh=True)
                if not token:
                    logger.info("No se pudo renovar token")
                    return False
                session.headers.update({"Authorization": f"Bearer {token}"})
                response = session.get(download_url, headers=headers, stream=True, timeout=300)

            # Si hicimos Range pero servidor respondió 200 OK (no soporta range), reiniciamos descarga
            if existing_size > 0 and response.status_code == 200:
                logger.info("El servidor no admite reanudación (respondió 200). Se reintentará descargando desde cero.")
                mode = 'wb'
                existing_size = 0

            # Si Range fue aceptada deberíamos recibir 206 Partial Content
            if response.status_code not in (200, 206):
                response.raise_for_status()

            # Determinar tamaño total esperado
            content_length = int(response.headers.get('content-length', 0))
            total_size = None
            content_range = response.headers.get('Content-Range')
            if content_range:
                # Formato: bytes start-end/total
                try:
                    total = content_range.split('/')[-1]
                    total_size = int(total)
                except Exception:
                    total_size = None

            if total_size is None:
                if existing_size and content_length:
                    total_size = existing_size + content_length
                elif content_length:
                    total_size = content_length

            total_gb = (total_size or 0) / (1024**3)
            if total_size:
                logger.info(f"Tamaño esperado: {total_gb:.2f} GB")
            else:
                logger.info(f"Tamaño desconocido")

            downloaded = existing_size
            chunk_size = 8192 * 1024  # 8 MB
            start_time = time.time()
            last_update = start_time

            # Escribir en disco (append o write)
            with open(output_file, mode) as f:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

                        # Progreso cada 2s
                        current_time = time.time()
                        if current_time - last_update >= 2 or (total_size and downloaded >= total_size):
                            elapsed = current_time - start_time
                            percent = (downloaded / total_size * 100) if total_size else 0
                            dl_gb = downloaded / (1024**3)
                            speed_mb = (downloaded / (1024**2)) / elapsed if elapsed > 0 else 0

                            # ETA
                            if speed_mb > 0 and total_size:
                                remaining_mb = (total_size - downloaded) / (1024**2)
                                eta_sec = remaining_mb / speed_mb
                                eta_str = f"{int(eta_sec//60)}m {int(eta_sec%60)}s"
                            else:
                                eta_str = "calculando..."



            final_size = os.path.getsize(output_file)

            # Validar descarga: si sabíamos total_size y final_size es cercano -> good
            if total_size and final_size < total_size * 0.99:
                logger.info(f"Descarga incompleta ({final_size}/{total_size} bytes)")
                # No borrar el archivo: lo dejamos para reanudar en siguiente ejecución
                return False

            elapsed = time.time() - start_time
            avg_speed = (final_size / (1024**2)) / elapsed if elapsed > 0 else 0
            logger.info(f"   ✅ Completado en {elapsed/60:.1f} min (promedio: {avg_speed:.1f} MB/s)")

            # Intentar extraer
            logger.info(f"   📦 Extrayendo archivo...")
            try:
                extract_dir = output_file.replace('.zip', '')  # Remover extensión .zip

                with zipfile.ZipFile(output_file, 'r') as zip_ref:
                    logger.info(f"   📦 Extrayendo {len(zip_ref.namelist())} archivos...")
                    _extract_zip_with_progress(zip_ref, download_dir, zip_ref.namelist())

                # Verificar integridad de la extracción
                manifest_file = os.path.join(extract_dir, 'manifest.safe')
                if not os.path.exists(manifest_file):
                    logger.info(f"Extracción incompleta: falta manifest.safe")
                    logger.info(f"Archivo .zip conservado en: {output_file}")
                    return False

                logger.info(f"Extraído a: {os.path.basename(extract_dir)}")

                # Eliminar .zip para ahorrar espacio
                try:
                    os.remove(output_file)
                    logger.info(f"Archivo .zip eliminado (ahorrando {final_size/(1024**3):.2f} GB)")
                except Exception:
                    pass

                # ISSUE #6: REGISTER IN DATABASE after successful download (S1 and S2)
                if DB_INTEGRATION_AVAILABLE:
                    try:
                        # Extract metadata from product dict
                        parsed = parse_product_name(product_name)
                        acquisition_date = parsed.get('date')
                        is_s2 = product_name.startswith('S2')
                        
                        if is_s2:
                            # Sentinel-2 registration
                            cloud_cover = None
                            aoi_coverage = None
                            
                            # Extract cloud cover from attributes if available
                            if 'Attributes' in product:
                                for attr in product.get('Attributes', []):
                                    if attr.get('Name') == 'cloudCover':
                                        try:
                                            cloud_cover = float(attr.get('Value', 0))
                                        except:
                                            pass
                            
                            if acquisition_date:
                                product_id = register_s2_download(
                                    scene_id=product_name,
                                    acquisition_date=acquisition_date,
                                    file_path=extracted_dir,
                                    cloud_cover_percent=cloud_cover
                                )
                                
                                if product_id:
                                    cloud_info = f", cloud_cover={cloud_cover:.1f}%" if cloud_cover else ""
                                    logger.info(f"   💾 Registered S2 in database (id={product_id}{cloud_info})")
                                else:
                                    logger.warning(f"   ⚠️  Failed to register S2 in database")
                            else:
                                logger.warning(f"   ⚠️  Missing date for S2 DB registration")
                        else:
                            # Sentinel-1 registration
                            orbit_direction = "UNKNOWN"
                            track_number = 0  # track_number = relative orbit for Sentinel-1

                            if 'Attributes' in product:
                                for attr in product.get('Attributes', []):
                                    if attr.get('Name') == 'orbitDirection':
                                        orbit_direction = attr.get('Value', 'UNKNOWN')
                                    elif attr.get('Name') == 'relativeOrbitNumber':
                                        track_number = int(attr.get('Value', 0))

                            if acquisition_date and track_number > 0:
                                product_id = register_slc_download(
                                    scene_id=product_name,
                                    acquisition_date=acquisition_date,
                                    orbit_direction=orbit_direction,
                                    track_number=track_number,
                                    file_path=extracted_dir
                                )

                                if product_id:
                                    logger.info(f"   💾 Registered S1 in database (id={product_id}, track={track_number})")
                                else:
                                    logger.warning(f"   ⚠️  Failed to register S1 in database")
                            else:
                                logger.warning(f"   ⚠️  Missing metadata for S1 DB registration (date={acquisition_date}, track={track_number})")

                    except Exception as e:
                        # Don't fail download if DB registration fails
                        logger.warning(f"   ⚠️  Could not register in database: {e}")

            except zipfile.BadZipFile:
                logger.info(f"Error al extraer: archivo .zip corrupto")
                # Mantener .zip para reanudar/inspección
                return True
            except Exception as e:
                logger.info(f"Error al extraer: {e}")
                logger.info(f"Archivo .zip conservado en: {output_file}")
                # No fallar si la extracción falla, el .zip está disponible
                return True

            session.close()
            return True

        except requests.exceptions.RequestException as e:
            # Retries en fallos de red
            logger.info(f"Error de red en intento {attempt}/{MAX_RETRIES}: {e}")
            if attempt >= MAX_RETRIES:
                logger.info("Número máximo de reintentos alcanzado. Conservando parcial para reanudar más tarde.")
                return False
            sleep_time = backoff ** attempt
            logger.info(f"   ⏳ Reintentando en {sleep_time}s...")
            time.sleep(sleep_time)
            continue
        except Exception as e:
            logger.info(f"Error inesperado: {e}")
            return False

    # Si salimos del loop sin éxito
    logger.info("Error: No se pudo completar la descarga")
    return False

def download_all_products(
    products: List[Dict],
    auth: CopernicusAuth,
    download_dir: str,
    product_type: str,
    auto_confirm: bool = False,
    orbit_direction: Optional[str] = None
) -> Dict:
    """Descarga todos los productos seleccionados"""

    os.makedirs(download_dir, exist_ok=True)

    logger.info(f"Directorio: {download_dir}")
    logger.info(f"Productos: {len(products)}")

    # Saltar confirmación si se usa --yes
    if not auto_confirm:
        response = input("¿Proceder con descarga? (y/n): ")
        if response.lower() != 'y':
            logger.info("❌ Cancelado")
            return {"successful": 0, "failed": 0}
    else:
        logger.info("✓ Auto-confirmado (--yes)")



    logger.info("" + "="*80)
    logger.info("DESCARGAS")
    logger.info("="*80)
    logger.info("💡 Puedes interrumpir con Ctrl+C y reanudar después")

    successful = 0
    failed = 0
    failed_products = []

    for idx, product in enumerate(products, 1):
        logger.info(f"{'='*80}")
        logger.info(f"[{idx}/{len(products)}]")
        logger.info(f"{'='*80}")

        if download_product(product, auth, download_dir):
            successful += 1
        else:
            failed += 1
            failed_products.append(product['Name'])

    # Resumen
    logger.info("" + "="*80)
    logger.info("RESUMEN")
    logger.info("="*80)
    logger.info(f"✅ Exitosas: {successful}")
    logger.info(f"❌ Fallidas: {failed}")

    if failed_products:
        logger.info("❌ Productos fallidos:")
        for name in failed_products[:10]:
            logger.info(f"   - {name}")
        if len(failed_products) > 10:
            logger.info(f"   ... y {len(failed_products) - 10} más")

    # Guardar metadata
    # Include orbit direction in filename if specified to avoid conflicts
    if orbit_direction:
        metadata_filename = f"metadata_{product_type.lower()}_{orbit_direction.lower()}.json"
    else:
        metadata_filename = f"metadata_{product_type.lower()}.json"
    metadata_file = os.path.join(download_dir, metadata_filename)
    with open(metadata_file, 'w', encoding='utf-8') as f:
        json.dump({
            'download_date': datetime.now().isoformat(),
            'total_products': len(products),
            'successful': successful,
            'failed': failed,
            'products': products
        }, f, indent=2, ensure_ascii=False)
    logger.info(f"📋 Metadata: {metadata_file}")

    return {
        "successful": successful,
        "failed": failed,
        "failed_products": failed_products
    }


def get_all_orbit_dates() -> Dict[str, set]:
    """
    Escanea `ORBITS_DIR` (directorio SNAP) y devuelve un dict: satellite -> set('YYYY-MM-DD')
    Espera estructura SNAP: ORBITS_DIR/POEORB/<SATELLITE>/<YYYY>/<MM>/*.EOF
    """
    orbits = {}
    if not os.path.exists(ORBITS_DIR):
        return orbits

    # Buscar en POEORB y RESORB
    for orbit_type in ['POEORB', 'RESORB']:
        orbit_type_dir = os.path.join(ORBITS_DIR, orbit_type)
        if not os.path.exists(orbit_type_dir):
            continue

        # Patrón: POEORB/S1X/YYYY/MM/*.EOF
        pattern = os.path.join(orbit_type_dir, '*', '*', '*', '*.EOF')
        for filepath in glob.glob(pattern):
            parts = os.path.normpath(filepath).split(os.sep)
            try:
                # Encontrar índice de POEORB/RESORB
                idx = parts.index(orbit_type)
                sat = parts[idx + 1]    # S1A, S1B, S1C
                year = parts[idx + 2]   # 2025
                month = parts[idx + 3]  # 10

                # Extraer día del nombre del archivo
                # S1C_OPER_AUX_POEORB_OPOD_20251105T071014_V20251015T225942_20251017T005942.EOF
                import re
                filename = os.path.basename(filepath)
                # Buscar fecha de validez (V...) en el nombre
                match = re.search(r'V(\d{8})', filename)
                if match:
                    validity_date = match.group(1)
                    day = validity_date[6:8]
                    date_str = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
                    orbits.setdefault(sat, set()).add(date_str)
            except Exception:
                continue

    return orbits


def filter_products_by_orbits(products: List[Dict], orbits_map: Dict[str, set], satellite: Optional[str]) -> List[Dict]:
    """
    Filtra la lista de productos dejando solo aquellos cuyo satélite y fecha
    aparecen en orbits_map. Usa parse_product_name para extraer satélite/fecha.
    Si `satellite` está especificado, también filtra por él.
    """
    if not products:
        return []

    filtered = []
    for prod in products:
        name = prod.get('Name', '')
        info = parse_product_name(name)
        sat = info.get('satellite')
        date_str = info.get('date_str')

        if not date_str or sat == 'N/A':
            continue

        # Si usuario especificó satélite, exigir coincidencia
        if satellite and sat != satellite:
            continue

        if sat in orbits_map and date_str in orbits_map[sat]:
            filtered.append(prod)

    return filtered


def is_slc_fully_processed(product_name: str, repository: InSARRepository, orbit_direction: Optional[str] = None) -> bool:
    """
    Verifica si un producto SLC ya está completamente procesado en el repositorio.

    Args:
        product_name: Nombre del producto SLC (ej: S1A_IW_SLC__1SDV_20251102T...)
        repository: Instancia de InSARRepository
        orbit_direction: Dirección de órbita (ASCENDING/DESCENDING), si se conoce

    Returns:
        True si el SLC ya está procesado y los productos finales existen
    """
    # Extraer información del producto
    info = parse_product_name(product_name)
    date_str = info.get('date_str')  # Formato: YYYY-MM-DD

    if not date_str or date_str == 'N/A':
        return False

    # Convertir de 'YYYY-MM-DD' a 'YYYYMMDD' para comparar con el repositorio
    slc_date = date_str.replace('-', '')  # '2025-11-03' -> '20251103'

    # Calcular track del SLC
    track = repository.extract_track_from_slc(product_name)
    if not track:
        return False

    # Determinar dirección de órbita si no se proporciona
    if not orbit_direction:
        # Intentar extraer del nombre del producto
        # Los productos Sentinel-1 no incluyen dirección de órbita en el nombre
        # Necesitaríamos consultar metadatos o buscar en ambas direcciones
        orbit_directions = ['ASCENDING', 'DESCENDING']
    else:
        orbit_directions = [orbit_direction]

    # Buscar en todas las direcciones y subswaths posibles
    for orbit_dir in orbit_directions:
        for subswath in ['IW1', 'IW2', 'IW3']:
            track_dir = repository.get_track_dir(orbit_dir, subswath, track)
            metadata_file = track_dir / 'metadata.json'

            if not metadata_file.exists():
                continue

            try:
                metadata = repository.load_metadata(orbit_dir, subswath, track)

                # Verificar si hay productos InSAR que usan esta fecha
                for product in metadata.get('insar_products', []):
                    master_date = product.get('master_date')
                    slave_date = product.get('slave_date')

                    # Si este SLC es usado como master o slave
                    if slc_date in [master_date, slave_date]:
                        # Verificar que el producto existe físicamente
                        product_file = track_dir / product['file']
                        if product_file.exists():
                            logger.debug(f"  ✓ {product_name[:50]} ya procesado en {orbit_dir}_{subswath}/t{track:03d}")
                            return True

                # Verificar productos polarimétricos
                for product in metadata.get('polarimetry_products', []):
                    if product.get('date') == slc_date:
                        product_file = track_dir / product['file']
                        if product_file.exists():
                            logger.debug(f"  ✓ {product_name[:50]} ya procesado (polarimetría) en {orbit_dir}_{subswath}/t{track:03d}")
                            return True

            except Exception as e:
                logger.debug(f"  Error verificando metadata para track {track}: {e}")
                continue

    return False


def calculate_missing_slcs_for_complete_processing(
    products: List[Dict],
    orbit_direction: str,
    subswaths: List[str] = ['IW1', 'IW2', 'IW3'],
    repo_base_dir: str = "data/processed_products",
    required_short_pairs: int = 2,
    required_long_pairs: int = 2
) -> Dict:
    """
    Calcula qué SLCs se necesitan descargar para completar el procesamiento.
    
    Para cada subswath, verifica:
    1. Qué fechas ya tienen productos InSAR procesados
    2. Cuántos pares short/long faltan por fecha
    3. Qué SLCs se necesitan para completar los pares faltantes
    
    Args:
        products: Lista de productos disponibles en Copernicus
        orbit_direction: Dirección de órbita
        subswaths: Lista de subswaths a verificar
        repo_base_dir: Directorio del repositorio
        required_short_pairs: Número de pares short requeridos por fecha (default: 2)
        required_long_pairs: Número de pares long requeridos por fecha (default: 2)
    
    Returns:
        Dict con información de SLCs necesarios por subswath/track
    """
    from collections import defaultdict
    
    repository = InSARRepository(repo_base_dir=repo_base_dir)
    
    logger.info("\n" + "=" * 80)
    logger.info("ANÁLISIS DE PROCESAMIENTO COMPLETO")
    logger.info("=" * 80)
    logger.info(f"Requerimientos por fecha:")
    logger.info(f"  - Pares cortos (6 días): {required_short_pairs}")
    logger.info(f"  - Pares largos (12 días): {required_long_pairs}")
    
    # Extraer fechas de productos disponibles
    available_dates = {}
    for product in products:
        info = parse_product_name(product['Name'])
        date_str = info['date_str'].replace('-', '')  # YYYYMMDD
        available_dates[date_str] = {
            'product': product,
            'info': info
        }
    
    all_dates = sorted(available_dates.keys())
    logger.info(f"\nFechas disponibles en Copernicus: {len(all_dates)}")
    if all_dates:
        logger.info(f"  Rango: {all_dates[0]} → {all_dates[-1]}")
    
    # Analizar cada subswath
    results = {}
    
    for subswath in subswaths:
        logger.info(f"\n--- Analizando {subswath} ---")
        
        # Buscar tracks con productos procesados para este subswath
        orbit_short = orbit_direction.lower()[:4]
        subswath_lower = subswath.lower()
        orbit_subswath_dir = repository.repo_base_dir / f"{orbit_short}_{subswath_lower}"
        
        if not orbit_subswath_dir.exists():
            logger.info(f"  No existe directorio para {orbit_short}_{subswath_lower}")
            continue
        
        track_dirs = sorted(orbit_subswath_dir.glob("t*"))
        
        for track_dir in track_dirs:
            track_num = int(track_dir.name[1:])
            metadata = repository.load_metadata(orbit_direction, subswath, track_num)
            
            stats = metadata.get('statistics', {})
            if stats.get('total_insar_short', 0) == 0 and stats.get('total_insar_long', 0) == 0:
                continue
            
            logger.info(f"\n  Track {track_num:03d}:")
            logger.info(f"    Productos actuales: {stats.get('total_insar_short', 0)} short, {stats.get('total_insar_long', 0)} long")
            
            # Analizar pares existentes por fecha
            pairs_by_date = defaultdict(lambda: {'short': [], 'long': []})
            processed_dates = set()
            
            for p in metadata['insar_products']:
                master_date = p['master_date']
                slave_date = p['slave_date']
                pair_type = p['pair_type']
                
                processed_dates.add(master_date)
                processed_dates.add(slave_date)
                
                if pair_type == 'short':
                    pairs_by_date[master_date]['short'].append(slave_date)
                else:
                    pairs_by_date[master_date]['long'].append(slave_date)
            
            logger.info(f"    Fechas procesadas: {len(processed_dates)}")
            
            # Calcular fechas faltantes y pares incompletos
            missing_dates = set(all_dates) - processed_dates
            incomplete_dates = []
            
            for date in sorted(processed_dates):
                short_count = len(pairs_by_date[date]['short'])
                long_count = len(pairs_by_date[date]['long'])
                
                if short_count < required_short_pairs or long_count < required_long_pairs:
                    incomplete_dates.append({
                        'date': date,
                        'short_missing': required_short_pairs - short_count,
                        'long_missing': required_long_pairs - long_count
                    })
            
            if missing_dates:
                logger.info(f"    Fechas NO procesadas: {len(missing_dates)}")
                logger.info(f"      Ejemplos: {', '.join(sorted(missing_dates)[:5])}")
            
            if incomplete_dates:
                logger.info(f"    Fechas con pares incompletos: {len(incomplete_dates)}")
                for item in incomplete_dates[:5]:
                    logger.info(f"      {item['date']}: falta {item['short_missing']} short, {item['long_missing']} long")
            
            # Calcular SLCs necesarios para completar
            needed_slcs = set()
            
            # 1. Añadir fechas completamente faltantes
            for date in missing_dates:
                if date in available_dates:
                    needed_slcs.add(date)
            
            # 2. Añadir fechas para completar pares incompletos
            for item in incomplete_dates:
                date = item['date']
                date_idx = all_dates.index(date) if date in all_dates else -1
                
                if date_idx == -1:
                    continue
                
                # Para pares short (6 días), necesitamos fecha-1 y fecha+1
                if item['short_missing'] > 0:
                    if date_idx > 0:
                        needed_slcs.add(all_dates[date_idx - 1])
                    if date_idx < len(all_dates) - 1:
                        needed_slcs.add(all_dates[date_idx + 1])
                
                # Para pares long (12 días), necesitamos fecha-2 y fecha+2
                if item['long_missing'] > 0:
                    if date_idx > 1:
                        needed_slcs.add(all_dates[date_idx - 2])
                    if date_idx < len(all_dates) - 2:
                        needed_slcs.add(all_dates[date_idx + 2])
            
            # Filtrar SLCs que ya están procesados
            needed_slcs_final = []
            for date in sorted(needed_slcs):
                if date not in processed_dates:
                    if date in available_dates:
                        needed_slcs_final.append(available_dates[date]['product'])
            
            if needed_slcs_final:
                logger.info(f"    ✓ SLCs necesarios: {len(needed_slcs_final)}")
                
                key = f"{orbit_short}_{subswath_lower}_t{track_num:03d}"
                results[key] = {
                    'orbit_direction': orbit_direction,
                    'subswath': subswath,
                    'track': track_num,
                    'needed_slcs': needed_slcs_final,
                    'missing_dates': len(missing_dates),
                    'incomplete_dates': len(incomplete_dates)
                }
            else:
                logger.info(f"    ✓ Procesamiento completo para este track")
    
    return results


def filter_products_for_complete_processing(
    products: List[Dict],
    orbit_direction: Optional[str] = None,
    repo_base_dir: str = "data/processed_products"
) -> Tuple[List[Dict], Dict]:
    """
    Filtra productos para descargar solo lo necesario para completar el procesamiento.
    
    Args:
        products: Lista de productos disponibles
        orbit_direction: Dirección de órbita
        repo_base_dir: Directorio base del repositorio
    
    Returns:
        Tuple de (productos_a_descargar, info_detallada)
    """
    if not products:
        return [], {}
    
    # Calcular qué SLCs se necesitan
    analysis = calculate_missing_slcs_for_complete_processing(
        products,
        orbit_direction,
        repo_base_dir=repo_base_dir
    )
    
    if not analysis:
        logger.info("\n✅ Todos los tracks tienen procesamiento completo")
        return [], {}
    
    # Recopilar todos los SLCs necesarios (sin duplicados)
    needed_products_ids = set()
    for key, info in analysis.items():
        for slc in info['needed_slcs']:
            needed_products_ids.add(slc['Id'])
    
    # Filtrar productos
    to_download = [p for p in products if p['Id'] in needed_products_ids]
    
    logger.info("\n" + "=" * 80)
    logger.info("RESUMEN DE DESCARGA")
    logger.info("=" * 80)
    logger.info(f"Tracks con procesamiento incompleto: {len(analysis)}")
    logger.info(f"SLCs únicos necesarios: {len(to_download)}")
    
    for key, info in analysis.items():
        logger.info(f"\n  {key}:")
        logger.info(f"    - {info['missing_dates']} fechas sin procesar")
        logger.info(f"    - {info['incomplete_dates']} fechas con pares incompletos")
        logger.info(f"    - {len(info['needed_slcs'])} SLCs a descargar")
    
    return to_download, analysis


def main():
    parser = argparse.ArgumentParser(
        description='Descarga de imágenes Copernicus Dataspace',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
MODO DE OPERACIÓN POR DEFECTO: INTELIGENTE (complete-processing)
  El script analiza el repositorio y descarga SOLO los SLCs necesarios para
  completar el procesamiento (objetivo: 2 pares cortos + 2 largos por fecha).

Ejemplos:

  # Modo inteligente (DEFAULT) - REQUIERE --orbit-direction
  python3 download_copernicus.py \\
    --collection SENTINEL-1 \\
    --aoi-geojson aoi/arenys_de_munt.geojson \\
    --orbit-direction DESCENDING \\
    --start-date 2025-07-01 \\
    --end-date 2025-12-31

  # Desactivar modo inteligente
  python3 download_copernicus.py \\
    --collection SENTINEL-1 \\
    --aoi-geojson aoi/arenys_de_munt.geojson \\
    --no-smart

  # Solo buscar (sin descargar)
  python3 download_copernicus.py \\
    --collection SENTINEL-1 \\
    --search-only
        """
    )

    parser.add_argument('--collection', choices=['SENTINEL-1', 'SENTINEL-2'],
                       default='SENTINEL-1')
    parser.add_argument('--product-type', default='SLC')
    parser.add_argument('--start-date', help='Fecha inicio (YYYY-MM-DD)')
    parser.add_argument('--end-date', help='Fecha fin (YYYY-MM-DD)')
    parser.add_argument('--satellites', nargs='+', choices=['S1A', 'S1B', 'S1C'],
                       help='Filtrar por satélite(s) específico(s) (ej: --satellites S1A S1C)')
    parser.add_argument('--orbit-direction', choices=['ASCENDING', 'DESCENDING'],
                       help='Filtrar por dirección de órbita')
    parser.add_argument('--orbit_type', default='POEORB',
                       help='Precisión de órbita para filtrar productos compatibles')
    parser.add_argument('--search-only', action='store_true')
    parser.add_argument('--yes', '-y', action='store_true',
                       help='Auto-confirmar descarga (no interactivo)')
    parser.add_argument('--months', type=int, default=6,
                       help='Número de meses hacia atrás para buscar productos (default: 6)')
    parser.add_argument('--aoi-geojson', type=str,
                       help='Archivo GeoJSON con el AOI (default: busca en aoi/ o aoi_arenys.geojson)')
    parser.add_argument('--min-coverage', type=float, default=10.0,
                       help='Porcentaje mínimo de cobertura del AOI requerido (default: 10%%)')
    parser.add_argument('--log-dir', default='logs',
                       help='Directorio donde guardar logs (default: logs/)')
    parser.add_argument('--skip-processed', action='store_true',
                       help='MODO SIMPLE: Solo omitir productos completamente procesados (usa --no-smart para desactivar modo inteligente)')
    parser.add_argument('--no-smart', action='store_true',
                       help='Desactivar modo inteligente (complete-processing). Por defecto el modo inteligente está ACTIVO.')
    parser.add_argument('--repo-dir', default='data/processed_products',
                       help='Directorio del repositorio de productos procesados (default: data/processed_products)')

    args = parser.parse_args()

    # Configurar logger con directorio especificado
    global logger
    logger = LoggerConfig.setup_script_logger(
        script_name="download_copernicus",
        log_dir=args.log_dir,
        level=logging.INFO
    )

    # Cargar AOI desde archivo GeoJSON
    global BBOX
    aoi_file = args.aoi_geojson

    if aoi_file:
        BBOX = load_aoi_from_geojson(aoi_file)
        if BBOX:
            logger.info(f"✅ AOI cargado desde {aoi_file}")
            logger.info(f"   Área: {BBOX['min_lon']:.4f},{BBOX['min_lat']:.4f} → {BBOX['max_lon']:.4f},{BBOX['max_lat']:.4f}")
    else:
        logger.error("No se especificó archivo GeoJSON para AOI (--aoi-geojson). Continuando sin filtro espacial.")


    # Banner
    logger.info("="*80)
    logger.info("DESCARGADOR COPERNICUS DATASPACE")
    logger.info("="*80)

    # Database integration status
    if DB_INTEGRATION_AVAILABLE:
        logger.info("🗄️  Database integration: ENABLED (satelit_metadata)")
        logger.info("   - Preventing duplicate downloads")
        logger.info("   - Tracking product traceability")
    else:
        logger.info("🗄️  Database integration: DISABLED (install satelit_db for tracking)")
    logger.info("="*80)

    # Obtener credenciales
    username = os.environ.get('COPERNICUS_USER')
    password = os.environ.get('COPERNICUS_PASSWORD')

    if not username or not password:
        logger.info("Credenciales requeridas")
        logger.info("Regístrate: https://dataspace.copernicus.eu/")
        username = input("Usuario: ")
        if not username:
            logger.info("❌ Usuario requerido")
            return 1

        import getpass
        password = getpass.getpass("Contraseña: ")
        if not password:
            logger.info("❌ Contraseña requerida")
            return 1

    # Crear objeto auth
    auth = CopernicusAuth(username, password)

    # Parsear fechas si se especifican
    start_date = None
    end_date = None

    if args.start_date:
        try:
            start_date = datetime.strptime(args.start_date, '%Y-%m-%d')
        except ValueError:
            logger.info(f"Fecha inválida: {args.start_date}")
            return 1

    if args.end_date:
        try:
            end_date = datetime.strptime(args.end_date, '%Y-%m-%d')
        except ValueError:
            logger.info(f"Fecha inválida: {args.end_date}")
            return 1

    # Fechas por defecto: últimos X meses
    if not start_date and not end_date:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=args.months*30)

    # Buscar
    products = search_products(
        auth=auth,
        collection=args.collection,
        product_type=args.product_type,
        start_date=start_date,
        end_date=end_date,
        orbit_direction=args.orbit_direction,
        aoi_name=BBOX.get('aoi_name', 'AOI') if BBOX else None
    )

    if not products:
        return 1

    # Filtrar por satélites si se especificó
    if args.satellites:
        original_count = len(products)
        products = [p for p in products if parse_product_name(p['Name'])['satellite'] in args.satellites]
        logger.info(f"   ️  Filtrando por satélites: {', '.join(args.satellites)}")
        logger.info(f"    {len(products)} productos coinciden (de {original_count} encontrados)")

        if not products:
            logger.info(f"  Ningún producto coincide con los satélites seleccionados: {', '.join(args.satellites)}")
            return 1

    # Filtrar por cobertura del AOI si está disponible
    if BBOX:
        products = filter_products_by_coverage(
            products,
            BBOX,
            min_coverage_pct=args.min_coverage,
            verbose=True
        )

        if not products:
            logger.info(f"️  Ningún producto cubre suficientemente el AOI (mínimo {args.min_coverage}%)")
            return 1

    # Filtrar por órbitas locales si existen (SOLO para Sentinel-1)
    orbits_map = get_all_orbit_dates()
    if args.collection == "SENTINEL-1" and orbits_map:
        filtered = filter_products_by_orbits(products, orbits_map, None)
        if not filtered:
            logger.info("️  Ningún producto Sentinel-1 coincide con las fechas de órbita locales.")
            logger.info("   Descarga órbitas primero: python scripts/download_orbits.py --start-date YYYY-MM-DD --end-date YYYY-MM-DD")
            return 1
        logger.info(f"   ✅ {len(filtered)} productos con órbita disponible (de {len(products)} encontrados)")
        products = filtered
    elif args.collection == "SENTINEL-1" and not orbits_map:
        logger.info(f"  No se detectaron órbitas locales en '{ORBITS_DIR}'")
        logger.info(f"   Descarga órbitas primero: python scripts/download_orbits.py --start-date {args.start_date} --end-date {args.end_date}")
        return 1
    elif args.collection == "SENTINEL-2":
        # Sentinel-2 no requiere archivos de órbita
        logger.info(f"     Sentinel-2: {len(products)} productos listos para descarga (no requiere órbitas)")

    # Marcar productos descargados/procesados y filtrar inteligentemente
    all_products_for_display = products[:]
    products_to_download = products
    
    if args.collection == "SENTINEL-1" and args.product_type == "SLC":
        # Verificar cuáles ya están descargados
        download_dir = os.path.join(
            BASE_DIR,
            f"{args.collection.lower().replace('-', '')}_{args.product_type.lower()}"
        )
        
        for product in all_products_for_display:
            product_name = product.get('Name', '')
            extracted_dir = os.path.join(download_dir, product_name)
            zip_file = os.path.join(download_dir, f"{product_name}.zip")
            
            # Verificar si está descargado
            if os.path.exists(extracted_dir):
                manifest_file = os.path.join(extracted_dir, 'manifest.safe')
                if os.path.exists(manifest_file):
                    product['_is_downloaded'] = True
            elif os.path.exists(zip_file):
                product['_is_downloaded'] = True
        
        # Modo INTELIGENTE por defecto: descargar solo lo necesario para completar procesamiento
        if not args.no_smart:
            if not args.orbit_direction:
                logger.info("⚠️  Modo inteligente requiere --orbit-direction")
                logger.info("   Usa --orbit-direction DESCENDING o ASCENDING")
                logger.info("   O desactiva modo inteligente con --no-smart")
                return 1
            
            logger.info("\n💡 Modo inteligente ACTIVO (desactivar con --no-smart)")
            
            products_to_download, analysis = filter_products_for_complete_processing(
                products,
                orbit_direction=args.orbit_direction,
                repo_base_dir=args.repo_dir
            )
            
            # Marcar productos en la lista de display
            needed_ids = {p['Id'] for p in products_to_download}
            for product in all_products_for_display:
                if product['Id'] in needed_ids:
                    product['_is_needed'] = True
                else:
                    product['_is_processed'] = True
            
            if not products_to_download:
                logger.info("\n✅ Procesamiento completo - no se necesitan más SLCs")
                return 0
        
        # Modo SIMPLE (solo con --skip-processed y --no-smart): omitir solo productos completamente procesados
        elif args.skip_processed:
            logger.info("\n💡 Modo simple activado (--skip-processed + --no-smart)")
            
            from insar_repository import InSARRepository
            repository = InSARRepository(repo_base_dir=args.repo_dir)
            
            to_download = []
            already_processed = []
            
            logger.info("Verificando productos ya procesados en el repositorio...")
            
            for product in products:
                product_name = product.get('Name', '')
                
                if is_slc_fully_processed(product_name, repository, args.orbit_direction):
                    product['_is_processed'] = True
                    already_processed.append(product)
                else:
                    product['_is_processed'] = False
                    to_download.append(product)
            
            if already_processed:
                logger.info(f"  ✓ {len(already_processed)} productos ya procesados (se omitirán)")
            
            if to_download:
                logger.info(f"  → {len(to_download)} productos nuevos a descargar")
            else:
                logger.info(f"  ✓ Todos los productos ya están procesados")
            
            products_to_download = to_download
            all_products_for_display = to_download + already_processed
            
            if not products_to_download:
                logger.info("✅ No hay productos nuevos para descargar - todos ya están procesados")
                return 0

    # Mostrar TODOS los productos encontrados
    logger.info("")
    logger.info("="*80)
    logger.info("PRODUCTOS ENCONTRADOS")
    logger.info("="*80)
    total_size = display_products(all_products_for_display)
    
    # Actualizar lista de productos a descargar
    products = products_to_download

    # Preparar directorio de descarga
    download_dir = os.path.join(
        BASE_DIR,
        f"{args.collection.lower().replace('-', '')}_{args.product_type.lower()}"
    )
    
    # Verificar espacio en el directorio de descarga (puede ser un enlace simbólico a NAS)
    try:
        free_gb = shutil.disk_usage(download_dir).free / (1024 ** 3)
        logger.info(f"💾 Espacio disponible en {download_dir}: {free_gb:.1f} GB")
    except FileNotFoundError:
        # Si el directorio no existe aún, verificar el padre
        parent_dir = os.path.dirname(download_dir)
        if not os.path.exists(parent_dir):
            parent_dir = BASE_DIR
        free_gb = shutil.disk_usage(parent_dir).free / (1024 ** 3)
        logger.info(f"💾 Espacio disponible en {parent_dir}: {free_gb:.1f} GB")
    
    if total_size > free_gb:
        logger.info("❌ Espacio insuficiente para descargar todos los productos")
        logger.info(f"   Necesitas: {total_size:.1f} GB")
        logger.info(f"   Disponible: {free_gb:.1f} GB")
        logger.info(f"   Faltante: {total_size - free_gb:.1f} GB")
        return 1

    if args.search_only:
        logger.info("✓ Búsqueda completa")
        return 0

    # Descargar (download_dir ya está definido arriba)
    stats = download_all_products(products, auth, download_dir, args.product_type, args.yes, args.orbit_direction)

    return 0 if stats['failed'] == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.info("  Interrumpido")
        logger.info("💡 Ejecuta de nuevo para reanudar")
        sys.exit(130)
