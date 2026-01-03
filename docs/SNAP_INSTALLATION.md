# SNAP 13.0.0 - Guía de Instalación

Esta guía explica cómo se instala y configura ESA SNAP 13.0.0 para el proyecto goshawk_ETL.

## Instalación Automática

El script `setup.sh` se encarga automáticamente de:

1. Verificar si SNAP 13.0.0 ya está instalado en `/opt/esa-snap`
2. Si no está instalado, descargarlo e instalarlo automáticamente
3. Configurar la interfaz Python `esa_snappy`
4. Verificar que todo funciona correctamente

### Uso

```bash
bash setup.sh
```

El script detectará si SNAP ya está instalado y:
- ✅ Si SNAP 13.0.0 está instalado: usará la instalación existente
- ⚠️  Si hay una versión diferente: preguntará si deseas actualizar
- ❌ Si no está instalado: lo descargará e instalará automáticamente

## Instalación Manual de SNAP

Si prefieres instalar SNAP manualmente:

### 1. Ejecutar el script de instalación

```bash
bash scripts/install_snap.sh
```

### 2. Verificar la instalación

```bash
# Verificar que gpt está disponible
gpt -h

# Verificar la interfaz Python
python -c "from esa_snappy import ProductIO; print('OK')"
```

## Detalles Técnicos

### Ubicación de instalación

- **SNAP**: `/opt/esa-snap/`
- **Versión**: 13.0.0
- **Python interface**: `esa_snappy` (instalado via pip)

### Cambios respecto a versiones anteriores

**Antes (SNAP 8.0 con snapista)**:
- Se usaba el paquete `snapista` de conda
- SNAP se instalaba dentro del entorno conda
- Interfaz Python: `snappy`

**Ahora (SNAP 13.0.0)**:
- SNAP se instala globalmente en `/opt/esa-snap`
- Un solo SNAP para todos los proyectos (ahorra ~2GB por proyecto)
- Interfaz Python moderna: `esa_snappy`
- Mejor integración y actualizaciones

### Ventajas del nuevo sistema

1. **Espacio en disco**: Una sola instalación para múltiples proyectos
2. **Actualizaciones**: Más fácil mantener SNAP actualizado
3. **Velocidad**: `esa_snappy` es más rápido que `snapista`
4. **Compatibilidad**: SNAP 13 incluye las últimas mejoras y correcciones

## Migración de código

Si tienes código antiguo que usa `snappy` o `snapista`, necesitarás actualizar los imports:

### Antes (SNAP 8.0)
```python
import snappy
from snappy import ProductIO, GPF
```

### Ahora (SNAP 13.0)
```python
import esa_snappy
from esa_snappy import ProductIO, GPF
```

El resto de la API es compatible, así que solo necesitas cambiar los imports.

## Solución de Problemas

### Error: "esa_snappy no encontrado"

```bash
# Reinstalar esa_snappy
pip install esa_snappy

# Reconfigurar
/opt/esa-snap/bin/snappy-conf $(which python)
```

### Error: "gpt no encontrado"

Agrega SNAP al PATH:

```bash
# Temporal (solo para la sesión actual)
export PATH="/opt/esa-snap/bin:$PATH"

# Permanente (agregar a ~/.bashrc o ~/.zshrc)
echo 'export PATH="/opt/esa-snap/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### SNAP no está en /opt/esa-snap

El script de instalación usa `/opt/esa-snap` por defecto. Si SNAP está en otra ubicación:

```bash
# Verificar ubicación
which gpt

# Si está en otra ubicación, crear symlink
sudo ln -s /tu/ubicacion/snap /opt/esa-snap
```

### Permisos insuficientes

La instalación en `/opt/` requiere permisos de superusuario:

```bash
# El script pedirá tu contraseña cuando sea necesario
sudo bash scripts/install_snap.sh
```

## Verificación Post-instalación

Ejecuta estos comandos para verificar que todo funciona:

```bash
# 1. Activar entorno conda
conda activate goshawk_etl

# 2. Verificar SNAP GPT
gpt -h

# 3. Verificar interfaz Python
python << EOF
from esa_snappy import ProductIO, GPF
print("✓ esa_snappy OK")
print("✓ ProductIO disponible")
print("✓ GPF disponible")
EOF

# 4. Verificar versión
cat /opt/esa-snap/VERSION.txt
```

Si todos los comandos funcionan correctamente, la instalación fue exitosa.

## Actualización de SNAP

Para actualizar a una nueva versión de SNAP en el futuro:

```bash
# 1. Descargar el nuevo instalador
wget https://download.esa.int/step/snap/X.Y/installers/esa-snap_all_unix_X_Y_Z.sh

# 2. Instalar (sobrescribirá la instalación anterior)
chmod +x esa-snap_all_unix_X_Y_Z.sh
sudo ./esa-snap_all_unix_X_Y_Z.sh -q -dir /opt/esa-snap

# 3. Reconfigurar esa_snappy
conda activate goshawk_etl
/opt/esa-snap/bin/snappy-conf $(which python)
```

## Recursos Adicionales

- [SNAP Official Website](http://step.esa.int/main/download/snap-download/)
- [SNAP Documentation](http://step.esa.int/docs/)
- [esa_snappy Documentation](https://senbox.atlassian.net/wiki/spaces/SNAP/pages/19300362/How+to+use+the+SNAP+API+from+Python)
- [SNAP Forum](https://forum.step.esa.int/)

## Soporte

Si encuentras problemas:

1. Revisa esta guía de solución de problemas
2. Consulta los logs en `logs/`
3. Busca en el [SNAP Forum](https://forum.step.esa.int/)
4. Abre un issue en el repositorio del proyecto
