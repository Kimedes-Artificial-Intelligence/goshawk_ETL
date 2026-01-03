#!/usr/bin/env python3
"""
Script: common_utils.py
Descripción: Funciones y clases compartidas por múltiples scripts
Uso: from common_utils import Colors, find_gpt, format_bytes, etc.

Este módulo proporciona utilidades comunes usadas en todo el proyecto:
- Colores ANSI para terminal
- Búsqueda de ejecutables (GPT, etc.)
- Formateo de datos (bytes, tiempo)
- Validación de archivos
"""

import os
import subprocess
from datetime import datetime
from pathlib import Path

# Constantes útiles
SENTINEL1_DATE_FORMAT = '%Y%m%dT%H%M%S'
ISO_DATE_FORMAT = '%Y-%m-%d'
ISO_DATETIME_FORMAT = '%Y-%m-%dT%H:%M:%S'

def find_gpt():
    """
    Encuentra el ejecutable GPT de SNAP
    
    Busca en ubicaciones comunes y en PATH
    
    Returns:
        str: Ruta al ejecutable GPT, None si no se encuentra
    """
    # Buscar en ubicaciones comunes
    possible_paths = [
        os.path.expanduser('~/snap/bin/gpt'),
        '/usr/local/snap/bin/gpt',
        '/opt/snap/bin/gpt',
        os.path.expanduser('~/Applications/snap/bin/gpt'),  # macOS
    ]
    
    for path in possible_paths:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    
    # Intentar encontrar en PATH
    try:
        result = subprocess.run(
            ['which', 'gpt'],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0:
            gpt_path = result.stdout.strip()
            if gpt_path and os.path.exists(gpt_path):
                return gpt_path
    except Exception:
        pass
    
    return None


def find_executable(name, possible_paths=None):
    """
    Encuentra un ejecutable por nombre
    
    Args:
        name: Nombre del ejecutable
        possible_paths: Lista opcional de rutas donde buscar
    
    Returns:
        str: Ruta al ejecutable, None si no se encuentra
    """
    # Buscar en PATH primero
    try:
        result = subprocess.run(
            ['which', name],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0:
            exec_path = result.stdout.strip()
            if exec_path and os.path.exists(exec_path):
                return exec_path
    except Exception:
        pass
    
    # Buscar en rutas específicas
    if possible_paths:
        for path in possible_paths:
            full_path = os.path.join(path, name)
            if os.path.exists(full_path) and os.access(full_path, os.X_OK):
                return full_path
    
    return None


def format_bytes(bytes_size):
    """
    Formatea tamaño en bytes a unidades legibles
    
    Args:
        bytes_size: Tamaño en bytes
    
    Returns:
        str: Tamaño formateado (ej: "1.5 GB")
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f} PB"


def format_duration(seconds):
    """
    Formatea duración en segundos a formato legible
    
    Args:
        seconds: Duración en segundos
    
    Returns:
        str: Duración formateada (ej: "2h 15m 30s")
    """
    if seconds < 60:
        return f"{int(seconds)}s"
    
    minutes = int(seconds // 60)
    remaining_seconds = int(seconds % 60)
    
    if minutes < 60:
        return f"{minutes}m {remaining_seconds}s"
    
    hours = int(minutes // 60)
    remaining_minutes = int(minutes % 60)
    
    if hours < 24:
        return f"{hours}h {remaining_minutes}m"
    
    days = int(hours // 24)
    remaining_hours = int(hours % 24)
    
    return f"{days}d {remaining_hours}h"


def format_timestamp(timestamp=None):
    """
    Formatea timestamp a string legible
    
    Args:
        timestamp: datetime object o None para usar ahora
    
    Returns:
        str: Timestamp formateado (ej: "2024-01-15 14:30:45")
    """
    if timestamp is None:
        timestamp = datetime.now()
    
    return timestamp.strftime('%Y-%m-%d %H:%M:%S')


def validate_file_exists(file_path, file_description="File"):
    """
    Valida que un archivo exista
    
    Args:
        file_path: Ruta al archivo
        file_description: Descripción del archivo para mensaje de error
    
    Returns:
        bool: True si existe, False si no
    
    Raises:
        FileNotFoundError: Si el archivo no existe
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"{file_description} not found: {file_path}")
    
    return True


def validate_directory_exists(dir_path, create=False):
    """
    Valida que un directorio exista, opcionalmente lo crea
    
    Args:
        dir_path: Ruta al directorio
        create: Si True, crea el directorio si no existe
    
    Returns:
        bool: True si existe o fue creado, False si no existe y create=False
    """
    if not os.path.exists(dir_path):
        if create:
            os.makedirs(dir_path, exist_ok=True)
            return True
        return False
    
    return True


def ensure_directory(dir_path):
    """
    Asegura que un directorio exista, creándolo si es necesario
    
    Args:
        dir_path: Ruta al directorio
    
    Returns:
        Path: Path object del directorio
    """
    path = Path(dir_path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_file_size(file_path):
    """
    Obtiene el tamaño de un archivo
    
    Args:
        file_path: Ruta al archivo
    
    Returns:
        int: Tamaño en bytes, 0 si el archivo no existe
    """
    try:
        return os.path.getsize(file_path)
    except (OSError, FileNotFoundError):
        return 0


def get_directory_size(dir_path):
    """
    Calcula el tamaño total de un directorio (recursivo)
    
    Args:
        dir_path: Ruta al directorio
    
    Returns:
        int: Tamaño total en bytes
    """
    total_size = 0
    
    try:
        for dirpath, dirnames, filenames in os.walk(dir_path):
            for filename in filenames:
                file_path = os.path.join(dirpath, filename)
                try:
                    total_size += os.path.getsize(file_path)
                except (OSError, FileNotFoundError):
                    pass
    except (OSError, FileNotFoundError):
        pass
    
    return total_size


def count_files(dir_path, pattern="*"):
    """
    Cuenta archivos en un directorio que coincidan con un patrón
    
    Args:
        dir_path: Ruta al directorio
        pattern: Patrón glob (default: "*" para todos)
    
    Returns:
        int: Número de archivos encontrados
    """
    try:
        return len(list(Path(dir_path).glob(pattern)))
    except (OSError, FileNotFoundError):
        return 0


def confirm_action(prompt, default=False):
    """
    Solicita confirmación del usuario
    
    Args:
        prompt: Texto del prompt
        default: Valor por defecto (True=Yes, False=No)
    
    Returns:
        bool: True si el usuario confirma, False si no
    """
    suffix = " (Y/n): " if default else " (y/N): "
    response = input(prompt + suffix).strip().lower()
    
    if not response:
        return default
    
    return response in ['y', 'yes', 'sí', 'si']


def safe_remove_file(file_path):
    """
    Elimina un archivo de forma segura (no falla si no existe)
    
    Args:
        file_path: Ruta al archivo
    
    Returns:
        bool: True si se eliminó, False si no existía
    """
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            return True
        return False
    except Exception:
        return False


def safe_remove_directory(dir_path):
    """
    Elimina un directorio de forma segura (no falla si no existe)
    
    Args:
        dir_path: Ruta al directorio
    
    Returns:
        bool: True si se eliminó, False si no existía
    """
    import shutil
    
    try:
        if os.path.exists(dir_path):
            shutil.rmtree(dir_path)
            return True
        return False
    except Exception:
        return False


def check_disk_space(path, required_gb=10):
    """
    Verifica que haya suficiente espacio en disco
    
    Args:
        path: Ruta para verificar
        required_gb: Espacio requerido en GB
    
    Returns:
        tuple: (tiene_espacio, espacio_disponible_gb)
    """
    import shutil
    
    try:
        stat = shutil.disk_usage(path)
        available_gb = stat.free / (1024**3)
        return (available_gb >= required_gb, available_gb)
    except Exception:
        return (False, 0)


def get_snap_orbits_dir():
    """
    Detecta el directorio de órbitas de SNAP

    Busca en las ubicaciones estándar de SNAP (conda y por defecto)

    Returns:
        Path: Path al directorio de órbitas de Sentinel-1 en SNAP
    """
    conda_snap = Path.home() / "miniconda3" / "envs" / "satelit_download" / "snap" / ".snap" / "auxdata" / "Orbits" / "Sentinel-1"
    default_snap = Path.home() / ".snap" / "auxdata" / "Orbits" / "Sentinel-1"

    return conda_snap if conda_snap.exists() else default_snap




