#!/usr/bin/env python3
"""
Script de verificación del sistema para Goshawk ETL
Verifica que todos los requisitos estén cumplidos
"""

import sys
import subprocess
import shutil
from pathlib import Path
import platform

class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    NC = '\033[0m'  # No Color

def check_command(cmd, name, required=True):
    """Verifica si un comando existe"""
    exists = shutil.which(cmd) is not None
    
    if exists:
        print(f"  {Colors.GREEN}✓{Colors.NC} {name}: {shutil.which(cmd)}")
        return True
    else:
        if required:
            print(f"  {Colors.RED}✗{Colors.NC} {name}: NO ENCONTRADO (requerido)")
        else:
            print(f"  {Colors.YELLOW}⚠{Colors.NC} {name}: NO ENCONTRADO (opcional)")
        return False

def check_python_package(package, import_name=None):
    """Verifica si un paquete Python está instalado"""
    if import_name is None:
        import_name = package
    
    try:
        __import__(import_name)
        print(f"  {Colors.GREEN}✓{Colors.NC} {package}")
        return True
    except ImportError:
        print(f"  {Colors.RED}✗{Colors.NC} {package}: NO INSTALADO")
        return False

def get_disk_space(path):
    """Obtiene espacio disponible en disco"""
    stat = shutil.disk_usage(path)
    free_gb = stat.free / (1024**3)
    return free_gb

def get_memory():
    """Obtiene memoria RAM disponible"""
    try:
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if 'MemTotal' in line:
                    mem_kb = int(line.split()[1])
                    mem_gb = mem_kb / (1024**2)
                    return mem_gb
    except:
        return None

def main():
    print(f"\n{Colors.BOLD}{Colors.BLUE}========================================")
    print("GOSHAWK ETL - Verificación del Sistema")
    print(f"========================================{Colors.NC}\n")
    
    issues = []
    
    # Sistema Operativo
    print(f"{Colors.BOLD}1. Sistema Operativo{Colors.NC}")
    os_name = platform.system()
    os_version = platform.release()
    
    if os_name == "Linux":
        print(f"  {Colors.GREEN}✓{Colors.NC} Linux {os_version}")
    elif os_name == "Darwin":
        print(f"  {Colors.GREEN}✓{Colors.NC} macOS {os_version}")
    else:
        print(f"  {Colors.RED}✗{Colors.NC} Sistema no soportado: {os_name}")
        issues.append("Sistema operativo no soportado")
    print()
    
    # Conda
    print(f"{Colors.BOLD}2. Gestor de Paquetes{Colors.NC}")
    has_mamba = check_command("mamba", "Mamba", required=False)
    has_conda = check_command("conda", "Conda", required=True)
    
    if not has_conda and not has_mamba:
        issues.append("conda o mamba no instalado")
        print(f"\n  {Colors.YELLOW}→ Instala desde: https://docs.conda.io/en/latest/miniconda.html{Colors.NC}")
    print()
    
    # Python
    print(f"{Colors.BOLD}3. Python{Colors.NC}")
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    
    if sys.version_info.major == 3 and sys.version_info.minor == 9:
        print(f"  {Colors.GREEN}✓{Colors.NC} Python {python_version} (compatible con SNAP)")
    elif sys.version_info.major == 3 and sys.version_info.minor < 9:
        print(f"  {Colors.YELLOW}⚠{Colors.NC} Python {python_version} (sistema)")
        print(f"    → El environment conda usará Python 3.9")
    else:
        print(f"  {Colors.YELLOW}ℹ{Colors.NC} Python {python_version} (sistema)")
        print(f"    → El environment conda usará Python 3.9 (requerido por SNAP)")
    print()
    
    # Paquetes Python críticos
    print(f"{Colors.BOLD}4. Paquetes Python{Colors.NC}")
    packages = [
        ("geopandas", "geopandas"),
        ("rasterio", "rasterio"),
        ("shapely", "shapely"),
        ("numpy", "numpy"),
        ("pandas", "pandas"),
        ("matplotlib", "matplotlib"),
        ("snapista", "snapista"),
    ]
    
    for package, import_name in packages:
        if not check_python_package(package, import_name):
            issues.append(f"Paquete Python faltante: {package}")
    print()
    
    # SNAP
    print(f"{Colors.BOLD}5. SNAP (Sentinel Application Platform){Colors.NC}")
    has_gpt = check_command("gpt", "SNAP GPT", required=True)
    
    if not has_gpt:
        issues.append("SNAP GPT no encontrado")
        print(f"\n  {Colors.YELLOW}→ Instalado automáticamente con snapista{Colors.NC}")
        print(f"  {Colors.YELLOW}→ Ubicación típica: ~/miniconda3/envs/goshawk_etl/snap/bin/gpt{Colors.NC}")
    print()
    
    # Espacio en disco
    print(f"{Colors.BOLD}6. Recursos del Sistema{Colors.NC}")
    
    # Disco
    free_space = get_disk_space("../..")
    print(f"  Espacio libre: {free_space:.1f} GB", end="")
    
    if free_space < 50:
        print(f" {Colors.RED}✗ INSUFICIENTE (mínimo 50GB){Colors.NC}")
        issues.append(f"Espacio en disco insuficiente: {free_space:.1f} GB")
    elif free_space < 100:
        print(f" {Colors.YELLOW}⚠ JUSTO (recomendado 200GB+){Colors.NC}")
    else:
        print(f" {Colors.GREEN}✓ OK{Colors.NC}")
    
    # RAM
    mem_gb = get_memory()
    if mem_gb:
        print(f"  Memoria RAM: {mem_gb:.1f} GB", end="")
        
        if mem_gb < 8:
            print(f" {Colors.RED}✗ INSUFICIENTE (mínimo 8GB){Colors.NC}")
            issues.append(f"RAM insuficiente: {mem_gb:.1f} GB")
        elif mem_gb < 16:
            print(f" {Colors.YELLOW}⚠ JUSTO (recomendado 16GB+){Colors.NC}")
        else:
            print(f" {Colors.GREEN}✓ OK{Colors.NC}")
    print()
    
    # Estructura de directorios
    print(f"{Colors.BOLD}7. Estructura del Proyecto{Colors.NC}")
    required_dirs = ["aoi", "data", "processing", "logs", "scripts", "docs"]
    
    for dir_name in required_dirs:
        dir_path = Path(dir_name)
        if dir_path.exists():
            print(f"  {Colors.GREEN}✓{Colors.NC} {dir_name}/")
        else:
            print(f"  {Colors.YELLOW}⚠{Colors.NC} {dir_name}/ (se creará automáticamente)")
    print()
    
    # Credenciales
    print(f"{Colors.BOLD}8. Configuración{Colors.NC}")
    env_file = Path("../../.env")
    
    if env_file.exists():
        print(f"  {Colors.GREEN}✓{Colors.NC} .env configurado")
        
        # Verificar si tiene valores por defecto
        with open(env_file) as f:
            content = f.read()
            if "tu_usuario" in content or "tu_password" in content:
                print(f"  {Colors.YELLOW}⚠{Colors.NC} Credenciales aún no configuradas")
                issues.append("Credenciales no configuradas en .env")
    else:
        print(f"  {Colors.YELLOW}⚠{Colors.NC} .env no existe (usar .env.example)")
        issues.append("Archivo .env faltante")
    print()
    
    # Resumen
    print(f"\n{Colors.BOLD}{Colors.BLUE}========================================")
    print("RESUMEN")
    print(f"========================================{Colors.NC}\n")
    
    if not issues:
        print(f"{Colors.GREEN}{Colors.BOLD}✅ SISTEMA LISTO{Colors.NC}")
        print(f"\nPróximos pasos:")
        print(f"  1. conda activate goshawk_etl")
        print(f"  2. python run_complete_workflow.py")
        return 0
    else:
        print(f"{Colors.YELLOW}{Colors.BOLD}⚠️  SE ENCONTRARON {len(issues)} PROBLEMA(S):{Colors.NC}\n")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")
        
        print(f"\n{Colors.BLUE}Solución rápida:{Colors.NC}")
        print(f"  bash setup.sh")
        return 1

if __name__ == "__main__":
    sys.exit(main())
