# ⚡ Quick Start - 3 Minutos

## Nuevo servidor? Solo 3 comandos:

```bash
# 1. Clonar
git clone https://github.com/tu-usuario/goshawk_ETL.git
cd goshawk_ETL

# 2. Setup (crea environment automáticamente)
bash setup.sh

# 3. Ejecutar
conda activate goshawk_etl
python run_complete_workflow.py
```

## Ya tienes el environment? Aún más rápido:

```bash
# Verificar que todo está OK
python check_system.py

# O test rápido
bash test.sh

# Ejecutar workflow
make workflow
# O:
python run_complete_workflow.py
```

## Comandos útiles:

```bash
make help       # Ver todos los comandos disponibles
make status     # Estado del proyecto
make workflow   # Ejecutar pipeline completo
make clean      # Limpiar archivos temporales
```

## Problemas?

```bash
# Reinstalar environment desde cero
conda env remove -n goshawk_etl -y
bash setup.sh

# Ver logs
tail -f logs/*.log

# Documentación
cat README.md
cat DEPLOYMENT.md
ls docs/
```

## Estructura básica:

```
goshawk_ETL/
├── aoi/               ← Pon tus GeoJSON aquí
├── data/              ← Datos descargados (automático)
├── processing/        ← Resultados (automático)
└── .env               ← Credenciales Copernicus
```

## Primera vez?

1. **Credenciales**: Edita `.env` con tu usuario/password de [dataspace.copernicus.eu](https://dataspace.copernicus.eu/)
2. **AOI**: Copia tu archivo GeoJSON a `aoi/`
3. **Ejecutar**: `python run_complete_workflow.py`

---

📚 **Más info**: `cat README.md` o `cat DEPLOYMENT.md`
