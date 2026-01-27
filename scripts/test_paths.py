#!/usr/bin/env python3
"""
Script de prueba para verificar resolución de rutas.
"""

from pathlib import Path
import sys

# Add scripts to path
script_dir = Path(__file__).parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

def test_paths():
    """Prueba de resolución de rutas."""
    repo_root = Path(__file__).parent.parent

    print("=" * 80)
    print("TEST DE RUTAS - Smart Workflow")
    print("=" * 80)

    print(f"\nRepositorio root: {repo_root}")
    print(f"Existe: {repo_root.exists()}")

    # Test rutas relativas
    test_paths = {
        "aoi/": repo_root / "aoi",
        "data/": repo_root / "data",
        "data/sentinel1_slc": repo_root / "data" / "sentinel1_slc",
        "data/processed_products": repo_root / "data" / "processed_products",
        "processing/": repo_root / "processing",
    }

    print("\n" + "-" * 80)
    print("RUTAS RELATIVAS:")
    print("-" * 80)

    for rel_path, abs_path in test_paths.items():
        exists = abs_path.exists()
        is_symlink = abs_path.is_symlink()

        print(f"\n{rel_path}")
        print(f"  Absoluta: {abs_path}")
        print(f"  Existe: {'✓' if exists else '✗'}")
        print(f"  Symlink: {'✓' if is_symlink else '✗'}")

        if is_symlink and exists:
            resolved = abs_path.resolve()
            print(f"  Resuelve a: {resolved}")

    # Test data symlink específicamente
    print("\n" + "-" * 80)
    print("SYMLINK 'data':")
    print("-" * 80)

    data_dir = repo_root / "data"
    if data_dir.is_symlink():
        target = data_dir.readlink()
        resolved = data_dir.resolve()
        print(f"  Symlink: {data_dir}")
        print(f"  Apunta a: {target}")
        print(f"  Resuelve a: {resolved}")
        print(f"  Target existe: {resolved.exists()}")

        # Listar contenido
        if resolved.exists():
            print(f"\n  Contenido de {resolved}:")
            for item in sorted(resolved.iterdir()):
                if item.is_dir():
                    print(f"    📁 {item.name}/")
                elif item.is_file() and item.stat().st_size < 1024*1024:  # < 1MB
                    print(f"    📄 {item.name}")
    else:
        print(f"  'data' no es un symlink: {data_dir}")

    print("\n" + "=" * 80)
    print("Test completado")
    print("=" * 80)

if __name__ == "__main__":
    test_paths()
