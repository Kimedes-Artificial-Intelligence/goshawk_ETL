import geopandas as gpd
import os
import unicodedata
import re
import sys

# --- CONFIGURACIÓN ---
# Ajusta esto si tu ruta es diferente
carpeta_entrada = "data/archivos_cnig"
carpeta_salida = "aoi"

if not os.path.exists(carpeta_salida):
    os.makedirs(carpeta_salida)

print(f"📂 Carpeta de trabajo: {os.path.abspath(carpeta_entrada)}")

# 1. SELECCIÓN DE ARCHIVO (Tu lógica ya funcionaba bien aquí)
try:
    todos = os.listdir(carpeta_entrada)
    # Buscamos el .shp que tenga 'recinto'
    archivo = next((f for f in todos if f.endswith(".shp") and "recinto" in f.lower()), None)
    
    if not archivo:
        print("❌ No encuentro el archivo 'recintos...shp'.")
        sys.exit()
        
    ruta = os.path.join(carpeta_entrada, archivo)
    print(f"✅ Archivo seleccionado: {archivo}")

except Exception as e:
    print(f"❌ Error buscando archivos: {e}")
    sys.exit()

try:
    # 2. CARGAR Y FILTRAR
    print("⚙️  Cargando archivo (esto tarda un poco)...")
    gdf = gpd.read_file(ruta)
    
    # Filtrar Cataluña (NATCODE empieza por 3409)
    # El código 34 es España, 09 es Cataluña.
    print("🔍 Filtrando municipios de Cataluña...")
    gdf_cat = gdf[gdf['NATCODE'].astype(str).str.startswith('3409')].copy()
    
    print(f"   -> {len(gdf_cat)} municipios filtrados.")

    # --- CORRECCIÓN DEL ERROR ---
    # Reseteamos el índice para que las filas vayan del 0 al 947
    # Esto evita el error "index out of bounds"
    gdf_cat = gdf_cat.reset_index(drop=True)
    # ----------------------------

    # 3. PROYECCIÓN (Lat/Lon)
    if gdf_cat.crs.to_string() != "EPSG:4326":
        print("🌎 Convirtiendo a WGS84 (Lat/Lon)...")
        gdf_cat = gdf_cat.to_crs("EPSG:4326")

    # 4. GUARDAR ARCHIVOS
    print("💾 Generando GeoJSONs individuales...")
    
    # Función limpieza nombre
    def limpiar(t):
        if not isinstance(t, str): return "desconocido"
        t = unicodedata.normalize('NFKD', t).encode('ASCII', 'ignore').decode('utf-8')
        t = re.sub(r'[^\w\s-]', '', t).strip().lower()
        return re.sub(r'[-\s]+', '_', t)

    count = 0
    col_nombre = 'NAMEUNIT' # Confirmado por tus logs anteriores

    for index, row in gdf_cat.iterrows():
        nombre_clean = limpiar(row[col_nombre])
        
        if not nombre_clean: continue
        
        # Creamos el GeoJSON individual
        # Al haber hecho reset_index, ahora 'index' coincide con la posición real
        gdf_single = gdf_cat.iloc[[index]].copy()
        
        # Limpiar columnas (solo name y geometry)
        gdf_single = gdf_single.rename(columns={col_nombre: 'name'})
        gdf_single = gdf_single[['name', 'geometry']]
        
        # Guardar
        salida = os.path.join(carpeta_salida, f"{nombre_clean}.geojson")
        gdf_single.to_file(salida, driver='GeoJSON')
        count += 1
        
        # Barra de progreso simple cada 100 archivos
        if count % 100 == 0:
            print(f"   ... {count} procesados")

    print(f"🎉 ¡ÉXITO! {count} archivos creados en '{carpeta_salida}'")

except Exception as e:
    print(f"❌ Error: {e}")
