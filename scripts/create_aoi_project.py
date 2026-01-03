#!/usr/bin/env python3
"""
Script: create_aoi_project.py
Descripción: Inicializa un nuevo proyecto de procesamiento para un AOI específico

Uso:
  python scripts/create_aoi_project.py aoi/barcelona_norte.geojson --name barcelona_norte
  python scripts/create_aoi_project.py aoi/mataro.geojson --name mataro_centro --copy-products

Este script:
1. Lee un archivo GeoJSON con el AOI
2. Crea la estructura de directorios processing/{aoi_name}/
3. Genera archivos de configuración con el AOI específico
4. Opcionalmente copia archivos de productos seleccionados
"""

import os
import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime
from logging_utils import LoggerConfig

# Logger global - se configurará en main()
logger = None


def load_aoi_geojson(geojson_path):
    """
    Carga archivo GeoJSON y extrae información del AOI

    Args:
        geojson_path: Ruta al archivo GeoJSON

    Returns:
        dict: Información del AOI (bbox, wkt, properties)
    """
    if not os.path.exists(geojson_path):
        logger.error(f"Error: No existe el archivo: {geojson_path}")
        return None

    try:
        with open(geojson_path, 'r') as f:
            geojson = json.load(f)

        # Validar estructura GeoJSON
        if 'features' not in geojson or len(geojson['features']) == 0:
            logger.error("Error: GeoJSON sin features")
            return None

        feature = geojson['features'][0]
        geometry = feature['geometry']
        properties = feature.get('properties', {})

        # Soportar Polygon y MultiPolygon
        if geometry['type'] == 'Polygon':
            coords = geometry['coordinates'][0]  # Exterior ring
        elif geometry['type'] == 'MultiPolygon':
            # Usar el polígono más grande del MultiPolygon
            logger.info(f"MultiPolygon detectado con {len(geometry['coordinates'])} polígonos")
            
            # Calcular área de cada polígono y usar el más grande
            largest_poly = None
            largest_area = 0
            
            for poly in geometry['coordinates']:
                exterior_ring = poly[0]
                # Calcular área aproximada (suma de productos de coordenadas)
                area = 0
                for i in range(len(exterior_ring) - 1):
                    area += (exterior_ring[i][0] * exterior_ring[i+1][1] - 
                            exterior_ring[i+1][0] * exterior_ring[i][1])
                area = abs(area) / 2
                
                if area > largest_area:
                    largest_area = area
                    largest_poly = exterior_ring
            
            if largest_poly is None:
                logger.error("Error: No se pudo extraer polígono de MultiPolygon")
                return None
            
            coords = largest_poly
            logger.info(f"Usando polígono más grande (área: {largest_area:.6f})")
        else:
            logger.error(f"Error: Tipo de geometría no soportado: {geometry['type']}")
            logger.error("Solo se soportan Polygon y MultiPolygon")
            return None

        # Calcular bbox
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]

        bbox = {
            'min_lon': min(lons),
            'max_lon': max(lons),
            'min_lat': min(lats),
            'max_lat': max(lats)
        }

        # Convertir a WKT (formato usado en config.txt)
        wkt_coords = ', '.join([f"{c[0]} {c[1]}" for c in coords])
        wkt = f"POLYGON(({wkt_coords}))"

        aoi_info = {
            'bbox': bbox,
            'wkt': wkt,
            'properties': properties,
            'geojson_path': geojson_path
        }

        return aoi_info

    except json.JSONDecodeError as e:
        logger.error(f"Error: JSON inválido: {e}")
        return None
    except Exception as e:
        logger.error(f"Error: {e}")
        return None


def create_project_structure(aoi_name, aoi_info, base_dir="processing"):
    """
    Crea estructura de directorios para el proyecto AOI

    Args:
        aoi_name: Nombre del proyecto/AOI
        aoi_info: Información del AOI
        base_dir: Directorio base (default: processing)

    Returns:
        Path: Ruta al directorio del proyecto
    """
    project_dir = Path(base_dir) / aoi_name

    # Crear directorios necesarios para el proyecto
    dirs_to_create = [
        # Directorio base y logs
        project_dir,
        project_dir / "logs",
    ]

    for dir_path in dirs_to_create:
        dir_path.mkdir(parents=True, exist_ok=True)

    logger.info(f"Estructura de directorios creada: {project_dir}")
    logger.info(f"  Nota: Las series de procesamiento se crearán automáticamente")
    logger.info(f"        al ejecutar el workflow (insar_desc_iw1/, insar_desc_iw2/, etc.)")

    return project_dir


def generate_config_template(project_dir, aoi_info, aoi_name):
    """
    [DEPRECATED] Esta función ya no se usa.
    
    Anteriormente generaba un config_template.txt para referencia,
    pero se eliminó para evitar confusión ya que cada serie genera
    su propio config.txt dinámicamente.
    
    Args:
        project_dir: Directorio del proyecto
        aoi_info: Información del AOI
        aoi_name: Nombre del proyecto

    Returns:
        None
    """
    # Función deprecada - no hace nada
    logger.warning(f"generate_config_template está deprecado y no hace nada")
    return None


def copy_geojson_to_project(project_dir, aoi_info):
    """
    Copia el archivo GeoJSON al directorio del proyecto

    Args:
        project_dir: Directorio del proyecto
        aoi_info: Información del AOI

    Returns:
        Path: Ruta al archivo GeoJSON copiado
    """
    import shutil

    src = Path(aoi_info['geojson_path'])
    dst = project_dir / "aoi.geojson"

    shutil.copy(src, dst)

    logger.info(f"GeoJSON copiado: {dst}")

    return dst


def update_products_json_with_aoi(products_json_path, aoi_info, output_path):
    """
    Actualiza archivo de productos con el nuevo AOI

    Args:
        products_json_path: Ruta al archivo de productos original
        aoi_info: Información del AOI
        output_path: Ruta de salida

    Returns:
        bool: True si exitoso
    """
    try:
        with open(products_json_path, 'r') as f:
            products = json.load(f)

        # Actualizar bbox
        products['aoi_bbox'] = aoi_info['bbox']

        # Guardar
        with open(output_path, 'w') as f:
            json.dump(products, f, indent=2)

        return True

    except Exception as e:
        logger.error(f"Error actualizando {products_json_path}: {e}")
        return False


def copy_product_configs(project_dir, aoi_info):
    """
    Copia y actualiza archivos de productos seleccionados

    Args:
        project_dir: Directorio del proyecto
        aoi_info: Información del AOI

    Returns:
        int: Número de archivos copiados
    """
    # Buscar archivos selected_products_*.json en la raíz
    product_files = list(Path(".").glob("selected_products_*.json"))

    if not product_files:
        logger.warning(f"No se encontraron archivos selected_products_*.json")
        logger.warning(f"Estos archivos se generarán al ejecutar select_multiswath_series.py")
        return 0

    copied = 0
    for src_file in product_files:
        dst_file = project_dir / src_file.name

        # Copiar y actualizar con nuevo AOI
        if update_products_json_with_aoi(src_file, aoi_info, dst_file):
            logger.info(f"Actualizado: {dst_file.name}")
            copied += 1
        else:
            logger.warning(f"Error: {dst_file.name}")

    return copied


def generate_readme(project_dir, aoi_name, aoi_info):
    """
    Genera README con información del proyecto

    Args:
        project_dir: Directorio del proyecto
        aoi_name: Nombre del proyecto
        aoi_info: Información del AOI

    Returns:
        Path: Ruta al README creado
    """
    readme_file = project_dir / "README.md"

    bbox = aoi_info['bbox']
    props = aoi_info['properties']

    readme_content = f"""# Proyecto: {aoi_name}

Proyecto de procesamiento InSAR para el AOI: **{props.get('name', aoi_name)}**

## Información del AOI

- **Nombre**: {props.get('name', aoi_name)}
- **Área**: {props.get('area', 'N/A')}
- **Descripción**: {props.get('description', 'N/A')}

### Coordenadas (WGS84)

- **Latitud**: {bbox['min_lat']:.6f} a {bbox['max_lat']:.6f}
- **Longitud**: {bbox['min_lon']:.6f} a {bbox['max_lon']:.6f}

### Archivos de configuración

- `aoi.geojson`: Geometría del AOI
- `selected_products_*.json`: Productos Sentinel-1 seleccionados por serie
- Cada serie genera su propio `config.txt` dinámicamente

## Estructura de directorios

```
{aoi_name}/
├── aoi.geojson                          # Geometría del AOI
├── logs/                                # Logs del proyecto
│
├── selected_products_desc_iw1.json      # Productos IW1 DESCENDING
├── selected_products_desc_iw2.json      # Productos IW2 DESCENDING
├── selected_products_desc_iw3.json      # Productos IW3 DESCENDING
├── selected_products_asce_iw1.json      # Productos IW1 ASCENDING
├── selected_products_asce_iw2.json      # Productos IW2 ASCENDING
├── selected_products_asce_iw3.json      # Productos IW3 ASCENDING
│
├── slc_preprocessed_desc/               # ✨ SLC DESCENDING compartido
│   ├── config.txt
│   ├── logs/
│   ├── products/
│   │   ├── iw1/                         # Productos IW1 pre-procesados DESCENDING
│   │   ├── iw2/                         # Productos IW2 pre-procesados DESCENDING
│   │   └── iw3/                         # Productos IW3 pre-procesados DESCENDING
│   └── raw_links/                       # Symlinks solo a SLC DESCENDING
│       ├── S1C_..._060010_DESCENDING.SAFE → ...
│       └── S1C_..._060011_DESCENDING.SAFE → ...
│
├── slc_preprocessed_asce/               # ✨ SLC ASCENDING compartido
│   ├── config.txt
│   ├── logs/
│   ├── products/
│   │   ├── iw1/                         # Productos IW1 pre-procesados ASCENDING
│   │   ├── iw2/                         # Productos IW2 pre-procesados ASCENDING
│   │   └── iw3/                         # Productos IW3 pre-procesados ASCENDING
│   └── raw_links/                       # Symlinks solo a SLC ASCENDING
│       └── S1C_..._174546_ASCENDING.SAFE → ...
│
│
├── insar_desc_iw1/                      # Serie DESCENDING IW1
│   ├── config.txt
│   ├── slc/                             # Symlinks a SLC (de esta serie)
│   ├── preprocessed_slc/ → ../slc_preprocessed_desc/products/iw1  # ✨ Symlink a SLC compartido
│   ├── insar/                           # Interferogramas procesados
│   ├── logs/
│   └── fusion/                          # Estadísticas temporales
│       ├── insar/                       # Coherencia InSAR
│       ├── sar/ → ../../grd_processed_desc/products  # ✨ Symlink a GRD compartido
│       ├── coherence_mean.tif           # 🎯 Coherencia media
│       ├── vv_std.tif                   # 🎯 Variabilidad VV
│       └── entropy_mean.tif             # 🎯 Entropía media
│
├── insar_desc_iw2/                      # Serie DESCENDING IW2
│   ├── preprocessed_slc/ → ../slc_preprocessed_desc/products/iw2
│   └── fusion/sar/ → ../../grd_processed_desc/products
│
├── insar_desc_iw3/                      # Serie DESCENDING IW3
│   ├── preprocessed_slc/ → ../slc_preprocessed_desc/products/iw3
│   └── fusion/sar/ → ../../grd_processed_desc/products
│
├── insar_asce_iw1/                      # Serie ASCENDING IW1
│   ├── preprocessed_slc/ → ../slc_preprocessed_asce/products/iw1  # ✨ Symlink a SLC ASCENDING
│   └── fusion/sar/ → ../../grd_processed_asce/products
│
├── insar_asce_iw2/                      # Serie ASCENDING IW2
│   ├── preprocessed_slc/ → ../slc_preprocessed_asce/products/iw2
│   └── fusion/sar/ → ../../grd_processed_asce/products
│
└── insar_asce_iw3/                      # Serie ASCENDING IW3
    ├── preprocessed_slc/ → ../slc_preprocessed_asce/products/iw3
    └── fusion/sar/ → ../../grd_processed_asce/products
```

### Ventajas de esta estructura

- **✨ SLC por órbita**: Separación DESCENDING/ASCENDING para coherencia total
- **🔗 Symlinks inteligentes**: Cada serie usa SLC y GRD de su órbita correspondiente
- **⚡ Procesamiento compartido**: SLC y GRD se procesan una vez por órbita
- **📊 Resultados independientes**: Cada serie genera su mapa de probabilidad

## Workflow de procesamiento

### Opción A: Workflow automático (RECOMENDADO)

```bash
# Ejecutar workflow completo desde la raíz del proyecto
python3 run_complete_workflow.py

# El script:
# 1. Descarga órbitas y productos
# 2. Crea el proyecto AOI
# 3. Genera configuraciones para cada órbita
# 4. Pre-procesa SLC (compartido)
# 6. Procesa todas las series InSAR
# 7. Calcula estadísticas temporales
```

### Opción B: Workflow manual

#### 1. Generar configuraciones de productos

```bash
# Generar para DESCENDING
python scripts/select_multiswath_series.py \\
    data/sentinel1_slc \\
    processing/{aoi_name}/aoi.geojson \\
    processing/{aoi_name} \\
    DESCENDING

# Generar para ASCENDING (opcional)
python scripts/select_multiswath_series.py \\
    data/sentinel1_slc \\
    processing/{aoi_name}/aoi.geojson \\
    processing/{aoi_name} \\
    ASCENDING
```

#### 2. Procesar cada serie

```bash
cd processing/{aoi_name}

# Series DESCENDING
python ../../process_insar_series.py selected_products_desc_iw1.json \\
    --output insar_desc_iw1 --full-pipeline
python ../../process_insar_series.py selected_products_desc_iw2.json \\
    --output insar_desc_iw2 --full-pipeline
python ../../process_insar_series.py selected_products_desc_iw3.json \\
    --output insar_desc_iw3 --full-pipeline

# Series ASCENDING (opcional)
python ../../process_insar_series.py selected_products_asce_iw1.json \\
    --output insar_asce_iw1 --full-pipeline
python ../../process_insar_series.py selected_products_asce_iw2.json \\
    --output insar_asce_iw2 --full-pipeline
python ../../process_insar_series.py selected_products_asce_iw3.json \\
    --output insar_asce_iw3 --full-pipeline
```

#### 3. Visualizar resultados

```bash
# Coherencia media de una serie
qgis insar_desc_iw1/fusion/coherence_mean.tif &

# Variabilidad VV de todas las series DESCENDING
qgis insar_desc_iw*/fusion/vv_std.tif &

# Entropía media de todas las series
qgis insar_*/fusion/entropy_mean.tif &

# Comparar coherencia DESCENDING vs ASCENDING
qgis insar_desc_iw1/fusion/coherence_mean.tif \\
     insar_asce_iw1/fusion/coherence_mean.tif &
```

## Generado

- **Fecha**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- **Script**: create_aoi_project.py
- **GeoJSON origen**: {aoi_info['geojson_path']}
"""

    with open(readme_file, 'w') as f:
        f.write(readme_content)

    logger.info(f"README generado: {readme_file}")

    return readme_file


def print_summary(aoi_name, project_dir, aoi_info, copied_products):
    """
    Imprime resumen del proyecto creado

    Args:
        aoi_name: Nombre del proyecto
        project_dir: Directorio del proyecto
        aoi_info: Información del AOI
        copied_products: Número de archivos de productos copiados
    """
    logger.info("")
    logger.info("="*80)
    logger.info(f"PROYECTO AOI CREADO")
    logger.info("="*80)
    logger.info(f"Proyecto: {aoi_name}")
    logger.info(f"Directorio: {project_dir}")
    logger.info("")


def main():
    parser = argparse.ArgumentParser(
        description='Inicializa un nuevo proyecto de procesamiento para un AOI específico',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  # Crear proyecto básico
  python scripts/create_aoi_project.py aoi/barcelona_norte.geojson --name barcelona_norte

  # Crear proyecto y copiar archivos de productos existentes
  python scripts/create_aoi_project.py aoi/mataro.geojson --name mataro --copy-products

  # Crear proyecto en directorio personalizado
  python scripts/create_aoi_project.py aoi/girona.geojson --name girona --base-dir projects
        """
    )

    parser.add_argument('geojson', help='Archivo GeoJSON con el AOI')
    parser.add_argument('--name', '-n', required=True, help='Nombre del proyecto')
    parser.add_argument('--base-dir', default='processing', help='Directorio base (default: processing)')
    parser.add_argument('--copy-products', action='store_true',
                       help='Copiar archivos selected_products_*.json existentes')
    parser.add_argument('--log-dir', default='logs',
                       help='Directorio donde guardar logs (default: logs/)')

    args = parser.parse_args()

    # Configurar logger con directorio especificado
    global logger
    logger = LoggerConfig.setup_script_logger(
        script_name="create_aoi_project",
        log_dir=args.log_dir,
        level=logging.INFO
    )

    # Banner
    logger.info(f"{'=' * 80}")
    logger.info(f"CREAR PROYECTO AOI")
    logger.info(f"{'=' * 80}")

    # Cargar GeoJSON
    logger.info(f"Cargando AOI: {args.geojson}")
    aoi_info = load_aoi_geojson(args.geojson)

    if not aoi_info:
        return 1

    logger.info(f"AOI cargado")
    logger.info(f"  Bbox: ({aoi_info['bbox']['min_lon']:.6f}, {aoi_info['bbox']['min_lat']:.6f}) a ({aoi_info['bbox']['max_lon']:.6f}, {aoi_info['bbox']['max_lat']:.6f})")
    logger.info("")

    # Crear estructura de proyecto
    project_dir = create_project_structure(args.name, aoi_info, args.base_dir)

    # Copiar GeoJSON
    copy_geojson_to_project(project_dir, aoi_info)

    # NOTA: config_template.txt eliminado para evitar confusión
    # Cada serie genera su propio config.txt dinámicamente
    # generate_config_template(project_dir, aoi_info, args.name)

    # Copiar archivos de productos (opcional)
    copied_products = 0
    if args.copy_products:
        logger.info(f"Copiando archivos de productos...")
        copied_products = copy_product_configs(project_dir, aoi_info)

    # Generar README
    generate_readme(project_dir, args.name, aoi_info)

    # Resumen
    print_summary(args.name, project_dir, aoi_info, copied_products)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.warning(f"Interrumpido por el usuario")
        sys.exit(130)
    except Exception as e:
        logger.error(f"ERROR: {str(e)}", exc_info=True)
        sys.exit(1)
