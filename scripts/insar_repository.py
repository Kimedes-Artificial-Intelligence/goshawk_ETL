#!/usr/bin/env python3
"""
Script: insar_repository.py
Descripción: Gestiona repositorio compartido de productos InSAR y Polarimétricos procesados

Estructura:
    data/processed_products/
    ├── desc_iw1/
    │   └── t088/
    │       ├── metadata.json
    │       ├── insar/
    │       │   ├── short/      # Pares contiguos (1→2, 2→3)
    │       │   └── long/       # Pares saltados (1→3, 2→4)
    │       └── polarimetry/
    │           ├── 20251102/   # Por fecha SLC
    │           └── 20251108/

Uso:
    # Listar repositorio
    python scripts/insar_repository.py --list

    # Verificar cobertura de AOI
    python scripts/insar_repository.py --check-coverage "POLYGON(...)" --orbit DESCENDING --subswath IW1

    # Añadir productos al repositorio
    python scripts/insar_repository.py --add-products processing/proyecto/insar_desc_iw1 --orbit DESCENDING --subswath IW1
"""

import os
import json
import re
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from shapely.geometry import shape, Polygon, box
from shapely import wkt

logger = logging.getLogger(__name__)


class InSARRepository:
    """Gestiona repositorio compartido de productos InSAR y Polarimétricos"""

    def __init__(self, repo_base_dir=None):
        """
        Args:
            repo_base_dir: Directorio raíz del repositorio (opcional)
                          Si no se especifica, usa data/processed_products desde la raíz del proyecto
        """
        if repo_base_dir is None:
            # Encontrar raíz del proyecto (directorio que contiene scripts/)
            # Este script está en scripts/insar_repository.py
            script_dir = Path(__file__).parent  # scripts/
            project_root = script_dir.parent    # raíz del proyecto
            repo_base_dir = project_root / "data" / "processed_products"

        self.repo_base_dir = Path(repo_base_dir).resolve()  # Convertir a ruta absoluta
        self.repo_base_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Repositorio: {self.repo_base_dir}")

    def extract_track_from_slc(self, slc_filename: str) -> Optional[int]:
        """
        Extrae track (órbita relativa) del nombre de archivo SLC Sentinel-1

        Formato: S1[ABC]_IW_SLC__1SDV_YYYYMMDDTHHMMSS_YYYYMMDDTHHMMSS_AAAAAA_BBBBBB_CCCC
                                                                      ^^^^^^
                                                                Absolute Orbit

        Cálculo track:
        - S1A/S1C: track = (absolute_orbit - 73) % 175 + 1
        - S1B:     track = (absolute_orbit - 27) % 175 + 1

        Args:
            slc_filename: Nombre del archivo SLC (puede incluir path)

        Returns:
            Número de track (1-175) o None si falla
        """
        # Extraer solo el basename
        basename = os.path.basename(slc_filename)

        # Pattern Sentinel-1 SLC
        pattern = r'S1([ABC])_IW_SLC__1S\w+_\d{8}T\d{6}_\d{8}T\d{6}_(\d{6})_'
        match = re.search(pattern, basename)

        if not match:
            logger.warning(f"No se pudo extraer órbita de: {basename}")
            return None

        satellite = match.group(1)  # A, B, o C
        absolute_orbit = int(match.group(2))

        # Calcular órbita relativa
        if satellite in ['A', 'C']:
            relative_orbit = (absolute_orbit - 73) % 175 + 1
        elif satellite == 'B':
            relative_orbit = (absolute_orbit - 27) % 175 + 1
        else:
            logger.error(f"Satélite desconocido: {satellite}")
            return None

        logger.debug(f"SLC {basename}: Satellite={satellite}, AbsOrbit={absolute_orbit}, Track={relative_orbit}")
        return relative_orbit

    def extract_date_from_slc(self, slc_filename: str) -> Optional[str]:
        """
        Extrae fecha del nombre de archivo SLC Sentinel-1
        
        Formato: S1[ABC]_IW_SLC__1SDV_YYYYMMDDTHHMMSS_YYYYMMDDTHHMMSS_AAAAAA_BBBBBB_CCCC
                                         ^^^^^^^^
                                      Fecha de inicio
        
        Args:
            slc_filename: Nombre del archivo SLC (puede incluir path)
        
        Returns:
            Fecha en formato YYYYMMDD o None si falla
        """
        # Extraer solo el basename
        basename = os.path.basename(slc_filename)
        
        # Pattern Sentinel-1 SLC
        pattern = r'S1[ABC]_IW_SLC__1S\w+_(\d{8})T\d{6}_'
        match = re.search(pattern, basename)
        
        if match:
            return match.group(1)  # YYYYMMDD
        
        return None

    def get_track_dir(self, orbit_direction: str, subswath: str, track: int) -> Path:
        """
        Obtiene directorio para orbit/subswath/track

        Args:
            orbit_direction: 'ASCENDING' o 'DESCENDING'
            subswath: 'IW1', 'IW2', o 'IW3'
            track: Número de track (1-175)

        Returns:
            Path al directorio del track
        """
        orbit_short = orbit_direction.lower()[:4]  # 'asce' o 'desc'
        subswath_lower = subswath.lower()  # 'iw1', 'iw2', 'iw3'

        track_dir = self.repo_base_dir / f"{orbit_short}_{subswath_lower}" / f"t{track:03d}"

        return track_dir

    def ensure_track_structure(self, orbit_direction: str, subswath: str, track: int):
        """Crea estructura de directorios para un track"""
        track_dir = self.get_track_dir(orbit_direction, subswath, track)

        (track_dir / "insar" / "short").mkdir(parents=True, exist_ok=True)
        (track_dir / "insar" / "long").mkdir(parents=True, exist_ok=True)
        (track_dir / "polarimetry").mkdir(parents=True, exist_ok=True)

        return track_dir

    def get_metadata_file(self, orbit_direction: str, subswath: str, track: int) -> Path:
        """Ruta al archivo metadata.json"""
        track_dir = self.get_track_dir(orbit_direction, subswath, track)
        return track_dir / "metadata.json"

    def load_metadata(self, orbit_direction: str, subswath: str, track: int) -> Dict:
        """
        Carga metadata del track

        Returns:
            Dict con metadata o estructura vacía si no existe
        """
        metadata_file = self.get_metadata_file(orbit_direction, subswath, track)

        if not metadata_file.exists():
            return {
                "track_id": f"{orbit_direction.lower()[:3]}_{subswath.lower()}_t{track:03d}",
                "orbit": {
                    "direction": orbit_direction,
                    "relative_orbit": track
                },
                "subswath": subswath,
                "insar_products": [],
                "polarimetry_products": [],
                "statistics": {
                    "total_insar_short": 0,
                    "total_insar_long": 0,
                    "total_polarimetry": 0,
                    "total_size_gb": 0.0
                },
                "processing_info": {
                    "created": datetime.now().isoformat(),
                    "last_updated": datetime.now().isoformat(),
                    "snap_version": "13.0.0"
                }
            }

        with open(metadata_file, 'r') as f:
            return json.load(f)

    def save_metadata(self, orbit_direction: str, subswath: str, track: int, metadata: Dict):
        """Guarda metadata actualizada"""
        metadata_file = self.get_metadata_file(orbit_direction, subswath, track)
        metadata['processing_info']['last_updated'] = datetime.now().isoformat()

        # Actualizar estadísticas
        metadata['statistics']['total_insar_short'] = sum(
            1 for p in metadata['insar_products'] if p.get('pair_type') == 'short'
        )
        metadata['statistics']['total_insar_long'] = sum(
            1 for p in metadata['insar_products'] if p.get('pair_type') == 'long'
        )
        metadata['statistics']['total_polarimetry'] = len(metadata['polarimetry_products'])
        metadata['statistics']['total_size_gb'] = (
            sum(p['size_gb'] for p in metadata['insar_products']) +
            sum(p['size_gb'] for p in metadata['polarimetry_products'])
        )

        # Actualizar rango temporal
        dates = set()
        for p in metadata['insar_products']:
            dates.add(p['master_date'])
            dates.add(p['slave_date'])
        for p in metadata['polarimetry_products']:
            dates.add(p['date'])

        if dates:
            sorted_dates = sorted(dates)
            metadata['temporal_range'] = {
                'start': sorted_dates[0],
                'end': sorted_dates[-1],
                'num_dates': len(sorted_dates)
            }

        # Eliminar campos obsoletos si existen
        metadata.pop('slc_products', None)
        metadata.pop('spatial_coverage', None)

        # Asegurar que existe el directorio
        metadata_file.parent.mkdir(parents=True, exist_ok=True)

        with open(metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)

        logger.debug(f"Metadata guardada: {metadata_file}")

    def check_aoi_coverage(self, orbit_direction: str, subswath: str,
                          aoi_wkt: str) -> Optional[Dict]:
        """
        Busca track que tenga productos procesados (sin verificar cobertura espacial)

        Args:
            orbit_direction: 'ASCENDING' o 'DESCENDING'
            subswath: 'IW1', 'IW2', o 'IW3'
            aoi_wkt: WKT del AOI (no utilizado actualmente)

        Returns:
            Dict con track info si encontrado, None si no
        """
        orbit_short = orbit_direction.lower()[:3]
        subswath_lower = subswath.lower()
        orbit_subswath_dir = self.repo_base_dir / f"{orbit_short}_{subswath_lower}"

        if not orbit_subswath_dir.exists():
            logger.debug(f"No existe: {orbit_subswath_dir}")
            return None

        # Buscar todos los tracks
        track_dirs = sorted(orbit_subswath_dir.glob("t*"))

        if not track_dirs:
            logger.debug(f"No hay tracks en: {orbit_subswath_dir}")
            return None

        # Buscar track con productos
        for track_dir in track_dirs:
            track_num = int(track_dir.name[1:])  # t088 → 88
            metadata = self.load_metadata(orbit_direction, subswath, track_num)

            stats = metadata.get('statistics', {})
            total_products = (stats.get('total_insar_short', 0) + 
                            stats.get('total_insar_long', 0) + 
                            stats.get('total_polarimetry', 0))

            if total_products > 0:
                logger.info(f"  ✓ Track {track_num} tiene productos procesados")
                logger.info(f"    InSAR: {stats.get('total_insar_short', 0)} short, "
                          f"{stats.get('total_insar_long', 0)} long")
                logger.info(f"    Polarimetría: {stats.get('total_polarimetry', 0)} productos")

                return {
                    'track': track_num,
                    'track_dir': track_dir,
                    'metadata': metadata,
                    'coverage_quality': 'unknown'
                }

        return None

    def add_products(self, source_workspace: Path, orbit_direction: str,
                    subswath: str, track: int,
                    spatial_coverage_wkt: Optional[str] = None) -> Dict:
        """
        Copia productos InSAR y Polarimétricos al repositorio

        Args:
            source_workspace: Workspace del proyecto (ej: processing/proyecto/insar_desc_iw1)
            orbit_direction: 'ASCENDING' o 'DESCENDING'
            subswath: 'IW1', 'IW2', 'IW3'
            track: Número de track
            spatial_coverage_wkt: WKT de cobertura (opcional)

        Returns:
            Dict con estadísticas de productos añadidos
        """
        source_workspace = Path(source_workspace)
        track_dir = self.ensure_track_structure(orbit_direction, subswath, track)
        metadata = self.load_metadata(orbit_direction, subswath, track)

        stats = {
            'insar_short_added': 0,
            'insar_long_added': 0,
            'polarimetry_added': 0,
            'insar_skipped': 0,
            'polarimetry_skipped': 0
        }

        # 1. Copiar productos InSAR
        logger.info(f"\n1. Copiando productos InSAR...")
        source_insar = source_workspace / "fusion" / "insar"

        if source_insar.exists():
            for dim_file in source_insar.glob("Ifg_*.dim"):
                # Determinar si es short o long
                pair_type = 'long' if '_LONG' in dim_file.stem else 'short'
                dest_subdir = track_dir / "insar" / pair_type
                dest_dim = dest_subdir / dim_file.name

                if dest_dim.exists():
                    logger.debug(f"  Ya existe: {dim_file.name}")
                    stats['insar_skipped'] += 1
                    continue

                # Copiar .dim
                shutil.copy2(dim_file, dest_dim)

                # Copiar .data
                data_dir = dim_file.with_suffix('.data')
                if data_dir.exists():
                    dest_data = dest_dim.with_suffix('.data')
                    shutil.copytree(data_dir, dest_data, dirs_exist_ok=True)

                # Añadir a metadata
                product_info = self._extract_insar_info(dest_dim, pair_type)
                metadata['insar_products'].append(product_info)

                if pair_type == 'short':
                    stats['insar_short_added'] += 1
                else:
                    stats['insar_long_added'] += 1

                logger.info(f"  ✓ {dim_file.name} ({product_info['size_gb']:.2f} GB)")

        # 2. Copiar productos Polarimétricos
        logger.info(f"\n2. Copiando productos Polarimétricos...")
        source_pol = source_workspace / "fusion" / "polarimetry"

        if source_pol.exists():
            for dim_file in source_pol.glob("*_HAAlpha.dim"):
                # Extraer fecha
                match = re.search(r'SLC_(\d{8})', dim_file.name)
                if not match:
                    logger.warning(f"  No se pudo extraer fecha de: {dim_file.name}")
                    continue

                date = match.group(1)
                dest_date_dir = track_dir / "polarimetry" / date
                dest_date_dir.mkdir(parents=True, exist_ok=True)
                dest_dim = dest_date_dir / dim_file.name

                if dest_dim.exists():
                    logger.debug(f"  Ya existe: {dim_file.name}")
                    stats['polarimetry_skipped'] += 1
                    continue

                # Copiar .dim
                shutil.copy2(dim_file, dest_dim)

                # Copiar .data
                data_dir = dim_file.with_suffix('.data')
                if data_dir.exists():
                    dest_data = dest_dim.with_suffix('.data')
                    shutil.copytree(data_dir, dest_data, dirs_exist_ok=True)

                # Añadir a metadata
                product_info = self._extract_polarimetry_info(dest_dim, date)
                metadata['polarimetry_products'].append(product_info)

                stats['polarimetry_added'] += 1
                logger.info(f"  ✓ {date}/{dim_file.name} ({product_info['size_gb']:.2f} GB)")

        # 3. Guardar metadata (estadísticas y temporal_range se actualizan automáticamente en save_metadata)
        self.save_metadata(orbit_direction, subswath, track, metadata)

        return stats

    def _extract_insar_info(self, dim_file: Path, pair_type: str) -> Dict:
        """Extrae info de producto InSAR"""
        basename = dim_file.stem.replace('_LONG', '')
        parts = basename.replace('Ifg_', '').split('_')

        master_date = parts[0] if len(parts) > 0 else 'unknown'
        slave_date = parts[1] if len(parts) > 1 else 'unknown'

        # Baseline temporal
        if master_date != 'unknown' and slave_date != 'unknown':
            from datetime import datetime
            d1 = datetime.strptime(master_date, '%Y%m%d')
            d2 = datetime.strptime(slave_date, '%Y%m%d')
            temporal_baseline_days = abs((d2 - d1).days)
        else:
            temporal_baseline_days = 0

        size_gb = self._calculate_size(dim_file)

        return {
            'file': f"insar/{pair_type}/{dim_file.name}",
            'master_date': master_date,
            'slave_date': slave_date,
            'pair_type': pair_type,
            'temporal_baseline_days': temporal_baseline_days,
            'size_gb': size_gb
        }

    def _extract_polarimetry_info(self, dim_file: Path, date: str) -> Dict:
        """Extrae info de producto polarimétrico"""
        size_gb = self._calculate_size(dim_file)

        return {
            'file': f"polarimetry/{date}/{dim_file.name}",
            'date': date,
            'decomposition': 'H-Alpha Dual Pol',
            'size_gb': size_gb
        }

    def _calculate_size(self, dim_file: Path) -> float:
        """Calcula tamaño total .dim + .data en GB"""
        size_bytes = 0

        if dim_file.exists():
            size_bytes += dim_file.stat().st_size

        data_dir = dim_file.with_suffix('.data')
        if data_dir.exists():
            for root, dirs, files in os.walk(data_dir):
                for file in files:
                    size_bytes += os.path.getsize(os.path.join(root, file))

        return size_bytes / (1024**3)

    def list_repository(self):
        """Lista contenido completo del repositorio"""
        print("\n" + "=" * 80)
        print("REPOSITORIO DE PRODUCTOS PROCESADOS")
        print("=" * 80 + "\n")

        combinations = [
            ('DESCENDING', 'IW1'), ('DESCENDING', 'IW2'), ('DESCENDING', 'IW3'),
            ('ASCENDING', 'IW1'), ('ASCENDING', 'IW2'), ('ASCENDING', 'IW3')
        ]

        total_tracks = 0
        total_insar = 0
        total_pol = 0
        total_size = 0.0

        for orbit, subswath in combinations:
            orbit_short = orbit.lower()[:3]
            subswath_lower = subswath.lower()
            orbit_dir = self.repo_base_dir / f"{orbit_short}_{subswath_lower}"

            if not orbit_dir.exists():
                continue

            track_dirs = sorted(orbit_dir.glob("t*"))

            if track_dirs:
                print(f"\n{orbit:12} {subswath}:")

                for track_dir in track_dirs:
                    track_num = int(track_dir.name[1:])
                    metadata = self.load_metadata(orbit, subswath, track_num)

                    stats = metadata.get('statistics', {})
                    short = stats.get('total_insar_short', 0)
                    long = stats.get('total_insar_long', 0)
                    pol = stats.get('total_polarimetry', 0)
                    size_gb = stats.get('total_size_gb', 0.0)

                    if short > 0 or long > 0 or pol > 0:
                        total_tracks += 1
                        total_insar += (short + long)
                        total_pol += pol
                        total_size += size_gb

                        print(f"  Track {track_num:03d}: {short:2}s + {long:2}l InSAR, {pol:2} Pol, {size_gb:6.2f} GB")

                        if metadata.get('temporal_range'):
                            tr = metadata['temporal_range']
                            print(f"             {tr['start']} → {tr['end']} ({tr['num_dates']} fechas)")

        print("\n" + "-" * 80)
        print(f"Total: {total_tracks} tracks, {total_insar} InSAR, {total_pol} Polarimetría, {total_size:.2f} GB")
        print("=" * 80 + "\n")


def main():
    """CLI para gestionar el repositorio"""
    import argparse

    parser = argparse.ArgumentParser(description='Gestión del repositorio de productos procesados')

    parser.add_argument('--list', action='store_true', help='Listar repositorio')
    parser.add_argument('--check-coverage', help='Verificar cobertura de AOI (WKT)')
    parser.add_argument('--orbit', choices=['ASCENDING', 'DESCENDING'], help='Dirección de órbita')
    parser.add_argument('--subswath', choices=['IW1', 'IW2', 'IW3'], help='Sub-swath')
    parser.add_argument('--track', type=int, help='Número de track (1-175)')
    parser.add_argument('--add-products', help='Workspace con productos a añadir')
    parser.add_argument('--coverage-wkt', help='WKT de cobertura espacial')

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

    repo = InSARRepository()

    if args.list:
        repo.list_repository()
        return 0

    elif args.check_coverage:
        if not args.orbit or not args.subswath:
            print("Error: --check-coverage requiere --orbit y --subswath")
            return 1

        result = repo.check_aoi_coverage(args.orbit, args.subswath, args.check_coverage)

        if result:
            print(f"\n✓ AOI cubierto por track {result['track']}")
            print(f"  Directorio: {result['track_dir']}")
            return 0
        else:
            print(f"\n✗ AOI NO cubierto en {args.orbit} {args.subswath}")
            return 1

    elif args.add_products:
        if not args.orbit or not args.subswath or args.track is None:
            print("Error: --add-products requiere --orbit, --subswath y --track")
            return 1

        stats = repo.add_products(
            args.add_products,
            args.orbit,
            args.subswath,
            args.track,
            args.coverage_wkt
        )

        print(f"\n✓ Productos añadidos:")
        print(f"  InSAR short: {stats['insar_short_added']} añadidos")
        print(f"  InSAR long: {stats['insar_long_added']} añadidos")
        print(f"  Polarimetría: {stats['polarimetry_added']} añadidos")
        print(f"  Existentes: {stats['insar_skipped'] + stats['polarimetry_skipped']} skipped")
        return 0

    else:
        parser.print_help()
        return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
