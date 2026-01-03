#!/usr/bin/env python3
"""
Script de MIGRACIÓN ÚNICA: Copia órbitas antiguas de data/orbits/ a SNAP

⚠️  NOTA IMPORTANTE:
Este script es solo para migración de órbitas antiguas que fueron descargadas
a data/orbits/ con versiones anteriores del código.

Las nuevas descargas con scripts/download_orbits.py van directamente al
directorio de SNAP y NO requieren este paso de migración.

Uso:
  python scripts/setup_local_orbits.py

Después de ejecutar este script una vez, puedes eliminar el directorio
data/orbits/ ya que ya no se usa.
"""

import os
import zipfile
import shutil
from pathlib import Path
import logging
import sys

# Agregar directorio scripts al path si es necesario
sys.path.insert(0, str(Path(__file__).parent))
from logging_utils import LoggerConfig

# Configurar logger
logger = LoggerConfig.setup_script_logger(
    script_name='setup_local_orbits',
    level=logging.INFO,
    console_level=logging.INFO
)


def setup_orbits_from_local():
    """
    Descomprime y copia órbitas desde data/orbits/ al directorio de SNAP
    """
    # Directorios
    data_orbits = Path("data/orbits")

    # Detectar directorio de SNAP
    conda_snap = Path.home() / "miniconda3" / "envs" / "satelit_download" / "snap" / ".snap" / "auxdata" / "Orbits" / "Sentinel-1"
    default_snap = Path.home() / ".snap" / "auxdata" / "Orbits" / "Sentinel-1"

    snap_base = conda_snap if conda_snap.exists() else default_snap

    if not data_orbits.exists():
        logger.error(f"❌ No existe el directorio {data_orbits}")
        return

    # Encontrar todos los archivos .EOF.zip
    zip_files = list(data_orbits.rglob("*.EOF.zip"))

    if not zip_files:
        logger.warning("⚠️  No se encontraron archivos .EOF.zip")
        return

    logger.info(f"Encontrados {len(zip_files)} archivos de órbita comprimidos")

    copied = 0
    skipped = 0
    errors = 0

    for zip_path in zip_files:
        try:
            # Extraer información del path
            # Ejemplo: data/orbits/S1C/2025/10/16/S1C_OPER_AUX_POEORB_...EOF.zip
            parts = zip_path.parts

            # Buscar índice de satélite (S1A, S1B, S1C)
            sat_idx = None
            for i, part in enumerate(parts):
                if part.startswith('S1'):
                    sat_idx = i
                    break

            if sat_idx is None or len(parts) < sat_idx + 3:
                logger.warning(f"⚠️  No se pudo determinar estructura: {zip_path}")
                continue

            satellite = parts[sat_idx]
            year = parts[sat_idx + 1]
            month = parts[sat_idx + 2]

            # Determinar tipo de órbita (POEORB o RESORB) desde el nombre del archivo
            filename = zip_path.name
            if 'POEORB' in filename:
                orbit_type = 'POEORB'
            elif 'RESORB' in filename:
                orbit_type = 'RESORB'
            else:
                logger.warning(f"⚠️  Tipo de órbita desconocido: {filename}")
                continue

            # Directorio de destino en SNAP
            dest_dir = snap_base / orbit_type / satellite / year / month
            dest_dir.mkdir(parents=True, exist_ok=True)

            # Nombre del archivo .EOF (sin .zip)
            eof_name = filename.replace('.EOF.zip', '.EOF')
            dest_file = dest_dir / eof_name

            # Verificar si ya existe
            if dest_file.exists():
                logger.debug(f"⏭️  Ya existe: {eof_name}")
                skipped += 1
                continue

            # Descomprimir
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # Buscar archivo .EOF dentro del zip
                eof_files = [f for f in zf.namelist() if f.endswith('.EOF')]

                if not eof_files:
                    logger.error(f"❌ No se encontró .EOF en {filename}")
                    errors += 1
                    continue

                # Extraer el contenido
                eof_content = zf.read(eof_files[0])

                # Guardar en destino
                with open(dest_file, 'wb') as f:
                    f.write(eof_content)

                size_kb = len(eof_content) / 1024
                logger.info(f"✅ Copiado: {satellite}/{year}/{month}/{eof_name} ({size_kb:.1f} KB)")
                copied += 1

        except Exception as e:
            logger.error(f"❌ Error procesando {zip_path}: {e}")
            errors += 1

    logger.info(f"""
═══════════════════════════════════════════════════════════
Resumen:
  ✅ Copiadas: {copied}
  ⏭️  Ya existían: {skipped}
  ❌ Errores: {errors}
  📁 Directorio SNAP: {snap_base}
═══════════════════════════════════════════════════════════
""")


if __name__ == '__main__':
    setup_orbits_from_local()
