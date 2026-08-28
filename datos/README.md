# datos/ â espejo de CSVs verificados del pipeline

Espejo de los CSVs de vacantes verificadas (Etapa 2) para que los scripts de
herramientas/ puedan leerlos directo vÃ­a git clone, sin depender del conector
de Drive. La fuente de trabajo sigue siendo la carpeta de Drive del proyecto;
este espejo se actualiza en cada corrida al publicar.

Los datos provienen de vacantes pÃºblicas (Adzuna, OCC, Indeed).

Formato: archivos `.csv.gz` (gzip). Leer con `pandas.read_csv(ruta, compression="gzip")` o descomprimir con `gunzip`.
