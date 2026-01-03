#!/usr/bin/env python3
"""
Script: cleanup_after_urban_crop.py
Descripción: Limpia archivos intermedios DESPUÉS del crop urbano
             Deja solo: fusion/pairs/ y urban_products/
Uso: python scripts/cleanup_after_urban_crop.py <workspace_dir>
"""

import os
import sys
import shutil
from pathlib import Path

def get_size_mb(path):
    """Calcula tamaño en MB"""
    total = 0
    if path.is_file():
        return path.stat().st_size / 1024 / 1024
    for item in path.rglob('*'):
        if item.is_file():
            total += item.stat().st_size
    return total / 1024 / 1024

def cleanup_workspace(workspace_dir, dry_run=False):
    """
    Limpia workspace dejando solo productos finales
    """
    workspace = Path(workspace_dir)
    
    if not workspace.exists():
        print(f"❌ No existe: {workspace_dir}")
        return False
    
    print("=" * 80)
    print(f"LIMPIEZA POST-CROP: {workspace.name}")
    print("=" * 80)
    
    if dry_run:
        print("🔍 MODO SIMULACIÓN (no se eliminará nada)")
    else:
        print("⚠️  MODO REAL (se eliminarán archivos)")
    print()
    
    # Directorios a eliminar por serie InSAR
    dirs_to_remove = [
        'slc',              # SLC originales (symlinks)
        'processed',        # Otros procesados
    ]

    # IMPORTANTE: NO eliminar preprocessed_slc porque puede ser symlink a caché global compartida
    dirs_to_remove_if_not_symlink = [
        'preprocessed_slc',  # Solo si NO es symlink a caché global
    ]
    
    # También eliminar archivos .dim y .data en fusion/insar
    # (solo mantener los TIF en pairs/)
    
    total_saved = 0
    total_removed = 0
    
    # Buscar series InSAR
    for insar_dir in workspace.glob('insar_*'):
        if not insar_dir.is_dir():
            continue
            
        print(f"\n📁 Serie: {insar_dir.name}")
        
        # Eliminar directorios intermedios (symlinks seguros)
        for dir_name in dirs_to_remove:
            dir_path = insar_dir / dir_name
            if dir_path.exists():
                # Si es symlink, solo eliminar el link (no el destino)
                if dir_path.is_symlink():
                    if dry_run:
                        print(f"  🔗 {dir_name}: symlink (eliminar solo enlace)")
                    else:
                        print(f"  🔗 {dir_name}: symlink (eliminando enlace)")
                        dir_path.unlink()  # Solo elimina el symlink, no el destino
                        total_removed += 1
                else:
                    size_mb = get_size_mb(dir_path)
                    if dry_run:
                        print(f"  🗑️  {dir_name}: {size_mb:.1f} MB (simular)")
                        total_saved += size_mb
                    else:
                        print(f"  🗑️  {dir_name}: {size_mb:.1f} MB (eliminando...)")
                        shutil.rmtree(dir_path)
                        total_saved += size_mb
                        total_removed += 1
        
        # Directorios preprocessed: solo eliminar si NO son symlinks
        for dir_name in dirs_to_remove_if_not_symlink:
            dir_path = insar_dir / dir_name
            if dir_path.exists():
                if dir_path.is_symlink():
                    # Proteger symlinks a caché global
                    target = dir_path.resolve()
                    print(f"  ✅ {dir_name}: symlink a caché global (PROTEGIDO)")
                    print(f"      → {target}")
                    # Solo eliminar el symlink (no el destino)
                    if not dry_run:
                        dir_path.unlink()
                        total_removed += 1
                else:
                    # Directorio real local, se puede eliminar
                    size_mb = get_size_mb(dir_path)
                    if dry_run:
                        print(f"  🗑️  {dir_name}: {size_mb:.1f} MB (simular)")
                        total_saved += size_mb
                    else:
                        print(f"  🗑️  {dir_name}: {size_mb:.1f} MB (eliminando...)")
                        shutil.rmtree(dir_path)
                        total_saved += size_mb
                        total_removed += 1
        
        # Eliminar .dim y .data en fusion/insar (mantener solo TIFs)
        fusion_insar = insar_dir / 'fusion' / 'insar'
        if fusion_insar.exists():
            # Eliminar archivos .dim
            for dim_file in fusion_insar.glob('*.dim'):
                size_mb = dim_file.stat().st_size / 1024 / 1024
                if dry_run:
                    print(f"  🗑️  {dim_file.name}: {size_mb:.1f} MB (simular)")
                    total_saved += size_mb
                else:
                    print(f"  🗑️  {dim_file.name}: {size_mb:.1f} MB")
                    dim_file.unlink()
                    total_saved += size_mb
                    total_removed += 1
            
            # Eliminar directorios .data
            for data_dir in fusion_insar.glob('*.data'):
                size_mb = get_size_mb(data_dir)
                if dry_run:
                    print(f"  🗑️  {data_dir.name}: {size_mb:.1f} MB (simular)")
                    total_saved += size_mb
                else:
                    print(f"  🗑️  {data_dir.name}: {size_mb:.1f} MB")
                    shutil.rmtree(data_dir)
                    total_saved += size_mb
                    total_removed += 1
            
            # Eliminar subdirectorio cropped si existe
            cropped_dir = fusion_insar / 'cropped'
            if cropped_dir.exists():
                size_mb = get_size_mb(cropped_dir)
                if dry_run:
                    print(f"  🗑️  cropped/: {size_mb:.1f} MB (simular)")
                    total_saved += size_mb
                else:
                    print(f"  🗑️  cropped/: {size_mb:.1f} MB")
                    shutil.rmtree(cropped_dir)
                    total_saved += size_mb
                    total_removed += 1
    
    # Eliminar logs viejos
    logs_dir = workspace / 'logs'
    if logs_dir.exists():
        size_mb = get_size_mb(logs_dir)
        if size_mb > 1:  # Solo si ocupa más de 1 MB
            if dry_run:
                print(f"\n🗑️  logs/: {size_mb:.1f} MB (simular)")
                total_saved += size_mb
            else:
                print(f"\n🗑️  logs/: {size_mb:.1f} MB")
                shutil.rmtree(logs_dir)
                total_saved += size_mb
                total_removed += 1
    
    # Resumen
    print()
    print("=" * 80)
    print("RESUMEN")
    print("=" * 80)
    
    if dry_run:
        print(f"💾 Espacio que se liberaría: {total_saved:.1f} MB ({total_saved/1024:.2f} GB)")
    else:
        print(f"✅ Elementos eliminados: {total_removed}")
        print(f"💾 Espacio liberado: {total_saved:.1f} MB ({total_saved/1024:.2f} GB)")
    
    print()
    print("📦 Estructura final conservada:")
    print("  ✓ urban_products/ (productos recortados a suelo urbano)")
    print("  ✓ insar_*/fusion/pairs/ (coherencia, VV, entropy por par)")
    print()
    
    return True


def main():
    if len(sys.argv) < 2:
        print("Uso: python scripts/cleanup_after_urban_crop.py <workspace_dir> [--dry-run]")
        print("\nEjemplo:")
        print("  python scripts/cleanup_after_urban_crop.py processing/el_far_demporda --dry-run")
        print("  python scripts/cleanup_after_urban_crop.py processing/el_far_demporda")
        return 1
    
    workspace_dir = sys.argv[1]
    dry_run = '--dry-run' in sys.argv
    
    return 0 if cleanup_workspace(workspace_dir, dry_run) else 1


if __name__ == '__main__':
    sys.exit(main())
