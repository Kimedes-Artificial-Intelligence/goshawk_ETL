#!/bin/bash
# Script para instalar y configurar ESA SNAP 13.0.0
# Detecta si SNAP ya está instalado y lo configura para Python

set -e  # Exit on error

SNAP_VERSION="13.0.0"
SNAP_INSTALL_DIR="/opt/esa-snap"
SNAP_DOWNLOAD_URL="https://download.esa.int/step/snap/13.0/installers/esa-snap_all_unix_13_0_0.sh"

echo "=========================================="
echo "ESA SNAP $SNAP_VERSION - Instalador"
echo "=========================================="
echo ""

# Función para verificar si SNAP está instalado
check_snap_installed() {
    if [ -d "$SNAP_INSTALL_DIR" ] && [ -f "$SNAP_INSTALL_DIR/bin/gpt" ]; then
        INSTALLED_VERSION=$(cat "$SNAP_INSTALL_DIR/VERSION.txt" 2>/dev/null || echo "unknown")
        return 0
    fi
    return 1
}

# Función para verificar versión de SNAP
check_snap_version() {
    local installed_version="$1"
    if [ "$installed_version" = "$SNAP_VERSION" ]; then
        return 0  # Versión correcta
    elif [ "$installed_version" = "unknown" ]; then
        return 1  # No se pudo determinar
    else
        return 2  # Versión diferente
    fi
}

# 1. Verificar si SNAP ya está instalado
echo "🔍 Verificando instalación de SNAP..."
if check_snap_installed; then
    INSTALLED_VERSION=$(cat "$SNAP_INSTALL_DIR/VERSION.txt" 2>/dev/null || echo "unknown")
    echo "✓ SNAP encontrado en: $SNAP_INSTALL_DIR"
    echo "  Versión instalada: $INSTALLED_VERSION"

    check_snap_version "$INSTALLED_VERSION"
    VERSION_CHECK=$?

    if [ $VERSION_CHECK -eq 0 ]; then
        echo "✓ Versión correcta ($SNAP_VERSION) ya instalada"
        SKIP_INSTALLATION=true
    elif [ $VERSION_CHECK -eq 2 ]; then
        echo "⚠️  Versión diferente detectada: $INSTALLED_VERSION (se requiere $SNAP_VERSION)"
        read -p "¿Actualizar a SNAP $SNAP_VERSION? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo "⚠️  Usando versión existente: $INSTALLED_VERSION"
            SKIP_INSTALLATION=true
        else
            SKIP_INSTALLATION=false
        fi
    else
        echo "⚠️  No se pudo verificar la versión"
        SKIP_INSTALLATION=false
    fi
else
    echo "❌ SNAP no encontrado en $SNAP_INSTALL_DIR"
    SKIP_INSTALLATION=false
fi
echo ""

# 2. Instalar SNAP si es necesario
if [ "$SKIP_INSTALLATION" != "true" ]; then
    echo "📦 Instalando ESA SNAP $SNAP_VERSION..."
    echo ""
    echo "⚠️  NOTA: La instalación requiere permisos de superusuario"
    echo "   Se instalará en: $SNAP_INSTALL_DIR"
    echo ""

    # Crear directorio temporal
    TMP_DIR=$(mktemp -d)
    cd "$TMP_DIR"

    echo "⬇️  Descargando SNAP installer..."
    echo "   URL: $SNAP_DOWNLOAD_URL"
    wget -q --show-progress "$SNAP_DOWNLOAD_URL" -O snap_installer.sh

    if [ ! -f snap_installer.sh ]; then
        echo "❌ Error: No se pudo descargar el instalador"
        rm -rf "$TMP_DIR"
        exit 1
    fi

    echo "✓ Descarga completa"
    echo ""

    # Hacer ejecutable el instalador
    chmod +x snap_installer.sh

    echo "🔧 Instalando SNAP (esto puede tardar 5-10 minutos)..."
    echo "   Instalación silenciosa en: $SNAP_INSTALL_DIR"

    # Instalar SNAP en modo no interactivo
    sudo ./snap_installer.sh -q -dir "$SNAP_INSTALL_DIR"

    # Limpiar archivos temporales
    cd -
    rm -rf "$TMP_DIR"

    echo "✓ SNAP instalado exitosamente"
    echo ""

    # Agregar SNAP al PATH (para el usuario actual)
    SHELL_RC=""
    if [ -f "$HOME/.bashrc" ]; then
        SHELL_RC="$HOME/.bashrc"
    elif [ -f "$HOME/.zshrc" ]; then
        SHELL_RC="$HOME/.zshrc"
    fi

    if [ -n "$SHELL_RC" ]; then
        if ! grep -q "esa-snap/bin" "$SHELL_RC"; then
            echo "" >> "$SHELL_RC"
            echo "# ESA SNAP" >> "$SHELL_RC"
            echo "export PATH=\"$SNAP_INSTALL_DIR/bin:\$PATH\"" >> "$SHELL_RC"
            echo "✓ SNAP agregado al PATH en $SHELL_RC"
        fi
    fi
fi

# 3. Verificar instalación de SNAP
echo "🧪 Verificando instalación de SNAP..."
if [ ! -f "$SNAP_INSTALL_DIR/bin/gpt" ]; then
    echo "❌ Error: gpt no encontrado en $SNAP_INSTALL_DIR/bin/"
    exit 1
fi

# Agregar temporalmente al PATH para esta sesión
export PATH="$SNAP_INSTALL_DIR/bin:$PATH"

# Verificar que gpt funciona
if command -v gpt &> /dev/null; then
    echo "✓ SNAP GPT disponible en PATH"
    GPT_VERSION=$(gpt -h 2>&1 | grep -i "SNAP Graph Processing Tool" | head -n1 || echo "SNAP GPT")
    echo "  $GPT_VERSION"
else
    echo "⚠️  SNAP instalado pero no en PATH. Ejecuta:"
    echo "   export PATH=\"$SNAP_INSTALL_DIR/bin:\$PATH\""
fi
echo ""

# 4. Configurar Python interface (esa_snappy)
echo "🐍 Configurando interfaz Python (esa_snappy)..."

# Detectar Python
if [ -n "$CONDA_PREFIX" ]; then
    PYTHON_EXEC="$CONDA_PREFIX/bin/python"
    echo "✓ Usando Python de conda: $PYTHON_EXEC"
elif command -v python3 &> /dev/null; then
    PYTHON_EXEC=$(which python3)
    echo "✓ Usando Python del sistema: $PYTHON_EXEC"
else
    echo "❌ Error: Python no encontrado"
    exit 1
fi

PYTHON_VERSION=$($PYTHON_EXEC --version 2>&1 | cut -d' ' -f2)
echo "  Versión: $PYTHON_VERSION"
echo ""

# Verificar si esa_snappy está instalado
echo "📦 Verificando paquete esa_snappy..."
if $PYTHON_EXEC -c "import esa_snappy" 2>/dev/null; then
    echo "✓ esa_snappy ya instalado"
else
    echo "⬇️  Instalando esa_snappy via pip..."
    $PYTHON_EXEC -m pip install esa_snappy --quiet
    echo "✓ esa_snappy instalado"
fi
echo ""

# Configurar snappy
echo "🔧 Configurando esa_snappy para SNAP $SNAP_VERSION..."
SNAPPY_CONF="$SNAP_INSTALL_DIR/bin/snappy-conf"

if [ ! -f "$SNAPPY_CONF" ]; then
    echo "❌ Error: snappy-conf no encontrado"
    exit 1
fi

echo "   Ejecutando: $SNAPPY_CONF $PYTHON_EXEC"
$SNAPPY_CONF "$PYTHON_EXEC" 2>&1 | grep -E "✓|Configuration|Done|Error" || true
echo ""

# 5. Verificar configuración final
echo "🧪 Verificación final..."
if $PYTHON_EXEC -c "from esa_snappy import ProductIO; print('✓ esa_snappy configurado correctamente')" 2>&1 | grep "✓"; then
    echo "✓ Configuración exitosa"
else
    echo "❌ Error en la configuración de esa_snappy"
    echo "   Intenta ejecutar manualmente:"
    echo "   $SNAPPY_CONF $PYTHON_EXEC"
    exit 1
fi
echo ""

echo "=========================================="
echo "✅ INSTALACIÓN COMPLETA"
echo "=========================================="
echo ""
echo "📝 Información:"
echo "   SNAP instalado en: $SNAP_INSTALL_DIR"
echo "   Versión: $SNAP_VERSION"
echo "   Python: $PYTHON_EXEC"
echo ""
echo "🔧 Para usar SNAP en nuevas sesiones:"
echo "   export PATH=\"$SNAP_INSTALL_DIR/bin:\$PATH\""
echo ""
echo "   O reinicia tu terminal para aplicar cambios permanentes"
echo ""
echo "🐍 Prueba la instalación:"
echo "   python -c 'from esa_snappy import ProductIO; print(\"OK\")'"
echo ""
