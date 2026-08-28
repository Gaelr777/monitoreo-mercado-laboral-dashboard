#!/usr/bin/env python3
"""
Etapa 2 — Verificación de empresa (versión determinística, sin LLM).

Reemplaza el trabajo fila-por-fila del subagente con un matching programático
contra empresas_verificadas_maestro.csv + empresas_confiables_<sector>.csv.
Solo las empresas NUEVAS (sin match) quedan en un archivo aparte para que el
LLM las revise — eso reduce el trabajo del agente de ~370 filas a típicamente
20-60 empresas únicas nuevas.

Uso:
  python3 etapa2_verificar.py \
      --raw vacantes_raw.csv [vacantes_raw2.csv ...] \
      --maestro empresas_verificadas_maestro.csv \
      --confiables empresas_confiables_sector.csv \
      --sector automotriz \
      --outdir ./salida

Salidas:
  salida/vacantes_<sector>_verificadas_auto.csv   (match con maestro/confiables)
  salida/vacantes_<sector>_pendientes.csv         (empresas nuevas → revisar con LLM)
  salida/descartados_<sector>_auto.csv            (placeholders/duplicados detectados)
  salida/empresas_nuevas_<sector>.csv             (lista única para revisar y anexar al maestro)
"""
import argparse, csv, re, sys, unicodedata
from pathlib import Path
import pandas as pd

# --- Normalización de nombre de empresa (misma lógica que el prompt de Etapa 2 v4) ---
LEGAL_SUFFIXES = r"(s\.?a\.?p?\.?i?\.?( de c\.?v\.?)?|s\.? de r\.?l\.?( de c\.?v\.?)?|s\.?c\.?|a\.?c\.?|inc\.?|llc|ltd\.?|corp\.?|co\.?|group|grupo)$"
PLACEHOLDERS = {
    "empresa confidencial", "confidencial", "importante empresa", "importante empresa del sector",
    "empresa lider", "empresa líder", "reclutamiento y seleccion", "reclutamiento", "n/a", "na", "",
    "importante grupo", "empresa importante", "compania confidencial", "compañia confidencial",
}

def normalizar(nombre: str) -> str:
    if not isinstance(nombre, str):
        return ""
    s = unicodedata.normalize("NFKD", nombre.lower().strip())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # quitar sufijos legales iterativamente
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"\b" + LEGAL_SUFFIXES, "", s).strip()
    return s

def es_placeholder(nombre_norm: str) -> bool:
    if nombre_norm in PLACEHOLDERS:
        return True
    if len(nombre_norm) < 3:
        return True
    return any(p in nombre_norm for p in ("confidencial", "importante empresa"))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", nargs="+", required=True)
    ap.add_argument("--maestro", required=True)
    ap.add_argument("--confiables", required=True)
    ap.add_argument("--sector", required=True)
    ap.add_argument("--prev-verified", nargs="*", default=[],
                    help="CSVs verificados de corridas anteriores (para detectar republicaciones por hash)")
    ap.add_argument("--outdir", default=".")
    a = ap.parse_args()
    out = Path(a.outdir); out.mkdir(parents=True, exist_ok=True)

    df = pd.concat([pd.read_csv(f, dtype=str) for f in a.raw], ignore_index=True)
    n0 = len(df)

    # 1. deduplicación interna por hash_contenido y por (titulo, empresa, ubicacion)
    df["_dup_hash"] = df.duplicated(subset=["hash_contenido"], keep="first")
    df["_dup_teu"] = df.duplicated(subset=["titulo_puesto", "empresa", "ubicacion"], keep="first")

    # 2. republicaciones vs corridas anteriores
    prev_hashes = set()
    for f in a.prev_verified:
        prev_hashes |= set(pd.read_csv(f, dtype=str)["hash_contenido"].dropna())
    df["_republicada"] = df["hash_contenido"].isin(prev_hashes)

    # 3. normalización y match de empresa
    df["empresa_normalizada"] = df["empresa"].map(normalizar)
    df["_placeholder"] = df["empresa_normalizada"].map(es_placeholder)

    maestro = pd.read_csv(a.maestro, dtype=str)
    confiables = pd.read_csv(a.confiables, dtype=str)
    col_m = "empresa_normalizada" if "empresa_normalizada" in maestro.columns else maestro.columns[0]
    col_c = "empresa_normalizada" if "empresa_normalizada" in confiables.columns else confiables.columns[0]
    conocidas = set(maestro[col_m].dropna().map(normalizar)) | set(confiables[col_c].dropna().map(normalizar))
    df["_conocida"] = df["empresa_normalizada"].isin(conocidas)

    descartar = df["_dup_hash"] | df["_dup_teu"] | df["_republicada"] | df["_placeholder"]
    verificadas = df[~descartar & df["_conocida"]].copy()
    pendientes = df[~descartar & ~df["_conocida"]].copy()
    descartados = df[descartar].copy()
    descartados["motivo"] = ""
    descartados.loc[descartados["_placeholder"], "motivo"] = "empresa_placeholder_generico"
    descartados.loc[descartados["_republicada"], "motivo"] = "republicacion_corrida_anterior"
    descartados.loc[descartados["_dup_teu"], "motivo"] = "duplicado_titulo_empresa_ubicacion"
    descartados.loc[descartados["_dup_hash"], "motivo"] = "duplicado_hash_contenido"

    aux = [c for c in df.columns if c.startswith("_")]
    verificadas.drop(columns=aux).to_csv(out / f"vacantes_{a.sector}_verificadas_auto.csv", index=False)
    pendientes.drop(columns=aux).to_csv(out / f"vacantes_{a.sector}_pendientes.csv", index=False)
    descartados.drop(columns=aux).to_csv(out / f"descartados_{a.sector}_auto.csv", index=False)
    (pendientes.groupby("empresa_normalizada")
     .agg(empresa=("empresa", "first"), vacantes=("empresa", "size"))
     .reset_index()
     .to_csv(out / f"empresas_nuevas_{a.sector}.csv", index=False))

    print(f"Total crudas: {n0}")
    print(f"Verificadas automáticamente: {len(verificadas)} ({len(verificadas)/n0:.0%})")
    print(f"Pendientes (empresa nueva, revisar con LLM): {len(pendientes)} "
          f"({pendientes['empresa_normalizada'].nunique()} empresas únicas)")
    print(f"Descartadas: {len(descartados)}")

if __name__ == "__main__":
    main()
