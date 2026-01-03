#!/usr/bin/env python3
"""
Script: extract_metrics_aoi.py
Descripción: Extrae métricas de productos globales para AOIs específicos

Extrae:
- InSAR: Coherencia, Intensidad (de interferogramas)
- SAR: VV, Entropía (de GRD)

Uso:
  python scripts/extract_metrics_aoi.py --aoi aoi/arenys_de_munt.geojson
  python scripts/extract_metrics_aoi.py --all-aois
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def find_gpt():
    """Encuentra el ejecutable GPT de SNAP"""
    gpt_paths = [
        '/usr/local/snap/bin/gpt',
        '/opt/snap/bin/gpt',
        os.path.expanduser('~/snap/bin/gpt'),
        'gpt'
    ]
    
    for gpt_path in gpt_paths:
        if os.path.exists(gpt_path) or subprocess.run(['which', gpt_path], 
                                                       capture_output=True).returncode == 0:
            return gpt_path
    
    return None


def geojson_to_wkt(geojson_file):
    """
    Convierte un archivo GeoJSON a WKT
    
    Args:
        geojson_file: Path al archivo GeoJSON
    
    Returns:
        str: Geometría en formato WKT
    """

    with open(geojson_file, 'r') as f:
        geojson = json.load(f)
    
    # Extraer geometry del primer feature
    if 'features' in geojson:
        geometry = geojson['features'][0]['geometry']
    elif 'geometry' in geojson:
        geometry = geojson['geometry']
    else:
        geometry = geojson
    
    # Convertir a WKT
    geom_type = geometry['type']
    
    if geom_type == 'Polygon':
        coords = geometry['coordinates'][0]  # Exterior ring
        wkt_coords = ', '.join([f"{lon} {lat}" for lon, lat in coords])
        return f"POLYGON (({wkt_coords}))"
    
    elif geom_type == 'MultiPolygon':
        polygons = []
        for polygon in geometry['coordinates']:
            coords = polygon[0]  # Exterior ring
            wkt_coords = ', '.join([f"{lon} {lat}" for lon, lat in coords])
            polygons.append(f"(({wkt_coords}))")
        return f"MULTIPOLYGON ({', '.join(polygons)})"
    
    else:
        raise ValueError(f"Tipo de geometría no soportado: {geom_type}")


def create_subset_xml(input_file, output_file, wkt_geometry):
    """
    Crea XML para hacer subset con AOI
    
    Args:
        input_file: Producto de entrada (.dim)
        output_file: Producto de salida (.dim)
        wkt_geometry: Geometría en formato WKT
    
    Returns:
        str: Contenido del XML
    """
    
    xml_content = f"""<graph id="Subset_AOI">
  <version>1.0</version>
  
  <node id="Read">
    <operator>Read</operator>
    <sources/>
    <parameters class="com.bc.ceres.binding.dom.XppDomElement">
      <file>{input_file}</file>
    </parameters>
  </node>
  
  <node id="Subset">
    <operator>Subset</operator>
    <sources>
      <sourceProduct refid="Read"/>
    </sources>
    <parameters class="com.bc.ceres.binding.dom.XppDomElement">
      <sourceBands/>
      <region/>
      <referenceBand/>
      <geoRegion>{wkt_geometry}</geoRegion>
      <subSamplingX>1</subSamplingX>
      <subSamplingY>1</subSamplingY>
      <fullSwath>false</fullSwath>
      <tiePointGridNames/>
      <copyMetadata>true</copyMetadata>
    </parameters>
  </node>
  
  <node id="Write">
    <operator>Write</operator>
    <sources>
      <sourceProduct refid="Subset"/>
    </sources>
    <parameters class="com.bc.ceres.binding.dom.XppDomElement">
      <file>{output_file}</file>
      <formatName>BEAM-DIMAP</formatName>
    </parameters>
  </node>
  
</graph>"""
    
    return xml_content


def execute_gpt(xml_content, gpt_path, description, timeout=600):
    """
    Ejecuta GPT con un XML dado
    
    Args:
        xml_content: Contenido del grafo XML
        gpt_path: Ruta al ejecutable GPT
        description: Descripción para logging
        timeout: Timeout en segundos
    
    Returns:
        tuple: (success: bool, message: str)
    """
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
        f.write(xml_content)
        xml_file = f.name
    
    try:
        cmd = [gpt_path, xml_file]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        os.unlink(xml_file)
        
        if result.returncode == 0:
            return True, "Completado"
        else:
            error_msg = result.stderr[:300] if result.stderr else "Error desconocido"
            return False, f"Error: {error_msg}"
    
    except subprocess.TimeoutExpired:
        os.unlink(xml_file)
        return False, f"Timeout (>{timeout}s)"
    except Exception as e:
        if os.path.exists(xml_file):
            os.unlink(xml_file)
        return False, f"Excepción: {str(e)}"


def extract_aoi_metrics(
    aoi_geojson,
    base_dir='processing',
    orbit='desc',
    subswath='iw1',
    dry_run=False
):
    """
    Extrae métricas de productos globales para un AOI específico
    
    Args:
        aoi_geojson: Path al archivo GeoJSON del AOI
        base_dir: Directorio base
        orbit: Órbita (desc/asc)
        subswath: Sub-swath (iw1/iw2/iw3)
        dry_run: Si True, solo muestra qué haría
    
    Returns:
        dict: Estadísticas de extracción
    """
    aoi_path = Path(aoi_geojson)
    
    if not aoi_path.exists():
        print(f"AOI no encontrado: {aoi_geojson}")
        return None
    
    # Obtener nombre del AOI
    aoi_name = aoi_path.stem
    
    # Crear workspace para este AOI
    workspace_dir = Path(base_dir) / "workspaces" / aoi_name
    workspace_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"{'='*80}")
    print(f"{Colors.BOLD}EXTRACCIÓN DE MÉTRICAS: {aoi_name.upper()}{Colors.NC}")
    print(f"{Colors.BOLD}{'='*80}{Colors.NC}\n")
    
    print(f"AOI: {aoi_geojson}")
    print(f"Workspace: {workspace_dir}")
    print(f"Órbita: {orbit.upper()}")
    print(f"Sub-swath: {subswath.upper()}")
    if dry_run:
        print(f"{Colors.YELLOW}MODO DRY-RUN{Colors.NC}")
    print()
    
    # Encontrar GPT
    gpt_path = find_gpt()
    if not gpt_path and not dry_run:
        print(f"{Colors.RED}✗ No se encontró GPT{Colors.NC}")
        return None
    
    if not dry_run:
        print(f"GPT: {gpt_path}\n")
    
    # Convertir GeoJSON a WKT
    try:
        wkt_geometry = geojson_to_wkt(aoi_path)
        print(f"AOI convertido a WKT ({len(wkt_geometry)} caracteres)\n")
    except Exception as e:
        print(f"{Colors.RED}✗ Error convirtiendo GeoJSON a WKT: {e}{Colors.NC}")
        return None
    
    stats = {
        "insar_extracted": 0,
        "grd_extracted": 0,
        "errors": 0
    }
    
    # 1. Extraer métricas InSAR
    print(f"{Colors.CYAN}1. Extrayendo métricas InSAR...{Colors.NC}\n")
    
    subswath_full = f"{orbit}_{subswath}"
    interferograms_dir = Path(base_dir) / "slc_global" / subswath_full / "interferograms"
    
    if interferograms_dir.exists():
        # Buscar todos los pares procesados
        pairs = list(interferograms_dir.glob("pair_*"))
        
        if pairs:
            print(f"  Pares encontrados: {len(pairs)}")
            
            for pair_dir in sorted(pairs):
                pair_name = pair_dir.name
                ifg_file = pair_dir / f"{pair_name}_ifg.dim"
                
                if not ifg_file.exists():
                    print(f"  ⚠️  {pair_name}: No se encontró .dim")
                    continue
                
                # Nombre de salida
                output_name = f"{pair_name}_aoi.dim"
                output_dir = workspace_dir / "insar" / subswath_full / pair_name
                output_dir.mkdir(parents=True, exist_ok=True)
                output_path = output_dir / output_name
                
                if output_path.exists():
                    print(f"  ✓ {pair_name}: Ya existe")
                    stats['insar_extracted'] += 1
                    continue
                
                print(f"  {pair_name}:")
                print(f"    Input: {ifg_file.name}")
                print(f"    Output: {output_dir.name}/{output_name}")
                
                if dry_run:
                    print(f"    [DRY-RUN] Se extraería subset")
                    stats['insar_extracted'] += 1
                    continue
                
                # Generar XML
                xml_content = create_subset_xml(
                    input_file=str(ifg_file.absolute()),
                    output_file=str(output_path),
                    wkt_geometry=wkt_geometry
                )
                
                # Ejecutar GPT
                success, message = execute_gpt(xml_content, gpt_path, f"InSAR {pair_name}", timeout=600)
                
                if success:
                    print(f"    {Colors.GREEN}✓ {message}{Colors.NC}")
                    stats['insar_extracted'] += 1
                else:
                    print(f"    {Colors.RED}✗ {message}{Colors.NC}")
                    stats['errors'] += 1
        else:
            print(f"  {Colors.YELLOW}⚠️  No hay pares procesados en {interferograms_dir}{Colors.NC}")
    else:
        print(f"  {Colors.YELLOW}⚠️  No existe {interferograms_dir}{Colors.NC}")
    
    # 2. Extraer métricas GRD
    print(f"\n{Colors.CYAN}2. Extrayendo métricas GRD...{Colors.NC}\n")
    
    grd_dir = Path(base_dir) / "grd_global" / orbit / "preprocessed"
    
    if grd_dir.exists():
        # Buscar todos los GRD preprocesados
        grd_products = list(grd_dir.glob("GRD_*.dim"))
        
        if grd_products:
            print(f"  Productos GRD encontrados: {len(grd_products)}")
            
            for grd_file in sorted(grd_products):
                grd_name = grd_file.stem  # GRD_20250829
                
                # Nombre de salida
                output_name = f"{grd_name}_aoi.dim"
                output_dir = workspace_dir / "grd" / orbit
                output_dir.mkdir(parents=True, exist_ok=True)
                output_path = output_dir / output_name
                
                if output_path.exists():
                    print(f"  ✓ {grd_name}: Ya existe")
                    stats['grd_extracted'] += 1
                    continue
                
                print(f"  {grd_name}:")
                print(f"    Input: {grd_file.name}")
                print(f"    Output: {output_name}")
                
                if dry_run:
                    print(f"    [DRY-RUN] Se extraería subset")
                    stats['grd_extracted'] += 1
                    continue
                
                # Generar XML
                xml_content = create_subset_xml(
                    input_file=str(grd_file.absolute()),
                    output_file=str(output_path),
                    wkt_geometry=wkt_geometry
                )
                
                # Ejecutar GPT
                success, message = execute_gpt(xml_content, gpt_path, f"GRD {grd_name}", timeout=600)
                
                if success:
                    print(f"    {Colors.GREEN}✓ {message}{Colors.NC}")
                    stats['grd_extracted'] += 1
                else:
                    print(f"    {Colors.RED}✗ {message}{Colors.NC}")
                    stats['errors'] += 1
        else:
            print(f"  {Colors.YELLOW}⚠️  No hay productos GRD preprocesados en {grd_dir}{Colors.NC}")
    else:
        print(f"  {Colors.YELLOW}⚠️  No existe {grd_dir}{Colors.NC}")
    
    # Resumen
    print(f"\n{Colors.BOLD}{'='*80}{Colors.NC}")
    print(f"{Colors.BOLD}RESUMEN - {aoi_name.upper()}{Colors.NC}")
    print(f"{Colors.BOLD}{'='*80}{Colors.NC}")
    print(f"InSAR extraídos: {Colors.GREEN}{stats['insar_extracted']}{Colors.NC}")
    print(f"GRD extraídos: {Colors.GREEN}{stats['grd_extracted']}{Colors.NC}")
    print(f"Errores: {Colors.RED if stats['errors'] > 0 else Colors.GREEN}{stats['errors']}{Colors.NC}")
    print(f"Workspace: {workspace_dir}")
    
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Extrae métricas de productos globales para AOIs específicos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  # Extraer métricas para un AOI
  python scripts/extract_metrics_aoi.py --aoi aoi/arenys_de_munt.geojson

  # Extraer para un AOI con órbita específica
  python scripts/extract_metrics_aoi.py --aoi aoi/arenys_de_munt.geojson --orbit desc --subswath iw1

  # Procesar todos los AOIs
  python scripts/extract_metrics_aoi.py --all-aois

  # Simulación
  python scripts/extract_metrics_aoi.py --aoi aoi/arenys_de_munt.geojson --dry-run
        """
    )
    
    parser.add_argument('--aoi', type=str,
                       help='Archivo GeoJSON del AOI')
    parser.add_argument('--all-aois', action='store_true',
                       help='Procesar todos los AOIs en aoi/')
    
    parser.add_argument('--orbit', type=str, choices=['desc', 'asc'], default='desc',
                       help='Órbita a usar (default: desc)')
    parser.add_argument('--subswath', type=str, choices=['iw1', 'iw2', 'iw3'], default='iw1',
                       help='Sub-swath a usar (default: iw1)')
    
    parser.add_argument('--dry-run', action='store_true',
                       help='Simulación (no procesar)')
    parser.add_argument('--base-dir', type=str, default='processing',
                       help='Directorio base (default: processing)')
    
    args = parser.parse_args()
    
    # Validaciones
    if not (args.aoi or args.all_aois):
        parser.print_help()
        return 1
    
    # Determinar qué AOIs procesar
    aois_to_process = []
    
    if args.all_aois:
        aoi_dir = Path('aoi')
        if aoi_dir.exists():
            aois_to_process = list(aoi_dir.glob('*.geojson'))
            if not aois_to_process:
                print(f"{Colors.YELLOW}⚠️  No se encontraron AOIs en {aoi_dir}{Colors.NC}")
                return 1
        else:
            print(f"{Colors.RED}✗ No existe directorio aoi/{Colors.NC}")
            return 1
    elif args.aoi:
        aois_to_process = [Path(args.aoi)]
    
    success = True
    
    # Procesar cada AOI
    for aoi_path in aois_to_process:
        stats = extract_aoi_metrics(
            aoi_geojson=str(aoi_path),
            base_dir=args.base_dir,
            orbit=args.orbit,
            subswath=args.subswath,
            dry_run=args.dry_run
        )
        
        if stats is None or stats.get('errors', 0) > 0:
            success = False
        
        print()
    
    return 0 if success else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}⚠️  Interrumpido por el usuario{Colors.NC}")
        sys.exit(130)
    except Exception as e:
        print(f"\n{Colors.RED}ERROR: {str(e)}{Colors.NC}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
