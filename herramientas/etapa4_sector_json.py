#!/usr/bin/env python3
"""
Etapa 4 — Generador del bloque sector_<key>.json con conteos EXACTOS (sin LLM).

Calcula todos los campos del esquema canónico (esquema_sector_sitio.md) por
pandas: fuentes, categorías (por diccionario de palabras clave), habilidades,
top empresas, cobertura geográfica, salario (percentiles reales, Adzuna
anual→mensual /12), evolución semanal, y la muestra vacantes_detalle con
filtro de recencia y muestreo proporcional por fuente.

Uso:
  python3 etapa4_sector_json.py \
      --verified vacantes_v1.csv [vacantes_v2.csv ...] \
      --sector Turismo --key turismo \
      --descartadas 58 \
      --categorias categorias_turismo.json \
      --habilidades habilidades_turismo.json \
      --out sector_turismo.json

categorias_*.json:  {"Alimentos y Bebidas": ["cocin", "chef", "mesero", ...], ...}
habilidades_*.json: {"tecnicas": {"Ventas y prospección comercial": ["venta", ...]},
                     "blandas":  {"Liderazgo": ["lider", "lidera"], ...},
                     "por_categoria": {"Alimentos y Bebidas":
                        {"tecnicas": [...], "blandas": [...]}, ...}}
"""
import argparse, json, re, unicodedata
from datetime import date, timedelta
from pathlib import Path
import pandas as pd

MESES = {1:"Ene",2:"Feb",3:"Mar",4:"Abr",5:"May",6:"Jun",7:"Jul",8:"Ago",9:"Sep",10:"Oct",11:"Nov",12:"Dic"}
ESTADOS = ["Aguascalientes","Baja California Sur","Baja California","Campeche","Chiapas","Chihuahua",
    "Ciudad de México","CDMX","Coahuila","Colima","Durango","Estado de México","Guanajuato","Guerrero",
    "Hidalgo","Jalisco","Michoacán","Morelos","Nayarit","Nuevo León","Oaxaca","Puebla","Querétaro",
    "Quintana Roo","San Luis Potosí","Sinaloa","Sonora","Tabasco","Tamaulipas","Tlaxcala","Veracruz",
    "Yucatán","Zacatecas"]
RECENCIA_DIAS = {"Adzuna": 10, "Indeed": 15, "OCC": 20}
UMBRAL_SALARIO_ANUAL = 90000  # arriba de esto en Adzuna se asume anual → /12

def norm(s):
    if not isinstance(s, str): return ""
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))

LEGAL_SUFFIX_RE = re.compile(
    r"[,.]?\s*(s\.?a\.?p?\.?i?\.?(\s*de\s*c\.?v\.?)?|s\.?\s*de\s*r\.?l\.?(\s*de\s*c\.?v\.?)?|"
    r"s\.?c\.?|a\.?c\.?|inc\.?|llc|ltd\.?|corp\.?|de\s*c\.?v\.?)\s*$")

def norm_empresa(s):
    s = norm(s)
    s = re.sub(r"[^\w\s]", " ", LEGAL_SUFFIX_RE.sub("", LEGAL_SUFFIX_RE.sub("", s)))
    return re.sub(r"\s+", " ", s).strip()

def detectar_estado(ubicacion):
    u = norm(ubicacion)
    for e in ESTADOS:
        if norm(e) in u:
            return "Ciudad de México" if e == "CDMX" else e
    if re.search(r",\s*(jal|qroo|n\.?\s?l|edo\.?\s?mex|cdmx)\.?$", u):
        m = {"jal":"Jalisco","qroo":"Quintana Roo","n l":"Nuevo León","nl":"Nuevo León",
             "edo mex":"Estado de México","cdmx":"Ciudad de México"}
        for k, v in m.items():
            if u.rstrip(".").endswith(k): return v
    return None

def clasificar(titulo, dicc):
    t = norm(titulo)
    for cat, kws in dicc.items():
        if any(norm(k) in t for k in kws):
            return cat
    return "Otro"

def contar_menciones(textos, dicc):
    out = []
    for skill, kws in dicc.items():
        n = sum(1 for t in textos if any(norm(k) in t for k in kws))
        if n > 0: out.append({"skill": skill, "menciones": n})
    return sorted(out, key=lambda x: -x["menciones"])

def salario_mensual(row):
    vals = [pd.to_numeric(row.get(c), errors="coerce") for c in ("salario_min", "salario_max")]
    vals = [v for v in vals if pd.notna(v) and v > 0]
    if not vals: return None
    v = sum(vals) / len(vals)
    if row.get("fuente") == "Adzuna" and v > UMBRAL_SALARIO_ANUAL:
        v /= 12
    return v

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verified", nargs="+", required=True)
    ap.add_argument("--sector", required=True); ap.add_argument("--key", required=True)
    ap.add_argument("--descartadas", type=int, default=0)
    ap.add_argument("--categorias", required=True); ap.add_argument("--habilidades", required=True)
    ap.add_argument("--hoy", default=None, help="YYYY-MM-DD (default: hoy)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    hoy = date.fromisoformat(a.hoy) if a.hoy else date.today()

    df = pd.concat([pd.read_csv(f, dtype=str) for f in a.verified], ignore_index=True)
    # normalizar etiqueta de fuente (adzuna/occ/indeed -> Adzuna/OCC/Indeed)
    df["fuente"] = df["fuente"].str.strip().str.lower().map(
        {"adzuna": "Adzuna", "occ": "OCC", "indeed": "Indeed"}).fillna(df["fuente"])
    # deduplicar por hash SOLO entre hashes reales (ignora vacíos/placeholder tipo "New")
    hash_valido = df["hash_contenido"].notna() & (df["hash_contenido"].str.len() >= 16)
    dup = hash_valido & df["hash_contenido"].duplicated(keep="first") & \
          df["hash_contenido"].where(hash_valido).duplicated(keep="first")
    df = df[~dup].reset_index(drop=True)
    cats = json.loads(Path(a.categorias).read_text())
    habs = json.loads(Path(a.habilidades).read_text())
    n = len(df)

    df["_fecha"] = pd.to_datetime(df["fecha_publicacion"], errors="coerce",
                                  format="mixed", utc=True).dt.tz_localize(None).dt.normalize()
    df["_estado"] = df["ubicacion"].map(detectar_estado)
    df["_cat"] = df["titulo_puesto"].map(lambda t: clasificar(t, cats))
    df["_texto"] = (df["titulo_puesto"].fillna("") + " " + df["habilidades_tecnicas"].fillna("")
                    + " " + df["habilidades_blandas"].fillna("")).map(norm)
    df["_sal"] = df.apply(salario_mensual, axis=1)
    df["_hab_explicita"] = df["habilidades_tecnicas"].fillna("").str.strip().ne("") | \
                           df["habilidades_blandas"].fillna("").str.strip().ne("")

    # fuentes / categorías / habilidades / empresas / estados — todo exacto
    fuentes = df["fuente"].value_counts().to_dict()
    cat_counts = df["_cat"].value_counts()
    categorias = [{"categoria": c, "vacantes": int(v), "pct": round(100*v/n, 1)}
                  for c, v in cat_counts.items()]
    tecnicas = contar_menciones(df["_texto"], habs["tecnicas"])[:15]
    blandas = contar_menciones(df["_texto"], habs["blandas"])[:15]
    df["_emp"] = df["empresa"].map(norm_empresa)
    emp = df[df["_emp"].ne("") & ~df["_emp"].str.contains(
        "confidencial|importante empresa|importante grupo|reclutamiento y seleccion", na=False)]
    def rep(s):
        vc = s.dropna().value_counts()
        return vc.index[0] if len(vc) else ""
    representante = emp.groupby("_emp")["empresa"].agg(rep)
    top_emp = [{"empresa": representante[e], "vacantes": int(v)}
               for e, v in emp["_emp"].value_counts().head(14).items()]
    est = df["_estado"].dropna().value_counts()
    top_estados = [{"estado": e, "vacantes": int(v)} for e, v in est.head(10).items()]

    con_sal = df["_sal"].dropna()
    salario = {"vacantes_con_dato": int(len(con_sal)), "pct_con_dato": round(100*len(con_sal)/n, 1),
               "mediana_mensual_mxn": int(con_sal.median()), "p25_mensual_mxn": int(con_sal.quantile(.25)),
               "p75_mensual_mxn": int(con_sal.quantile(.75))} if len(con_sal) else \
              {"vacantes_con_dato": 0, "pct_con_dato": 0.0, "mediana_mensual_mxn": None,
               "p25_mensual_mxn": None, "p75_mensual_mxn": None}

    hoy_ts = pd.Timestamp(hoy)
    sem = df.dropna(subset=["_fecha"]).copy()
    sem = sem[(sem["_fecha"] >= hoy_ts - pd.Timedelta(days=120)) & (sem["_fecha"] <= hoy_ts)]
    sem["_lunes"] = sem["_fecha"] - pd.to_timedelta(sem["_fecha"].dt.weekday, unit="D")
    evo = [{"semana": f"{d.day:02d} {MESES[d.month]}", "vacantes": int(v)}
           for d, v in sem.groupby("_lunes").size().sort_index().items()]

    # vacantes_detalle: recencia por fuente + muestreo proporcional (max 60)
    def vigente(r):
        lim = RECENCIA_DIAS.get(r["fuente"], 10)
        return pd.notna(r["_fecha"]) and (hoy - r["_fecha"].date()) <= timedelta(days=lim)
    vig = df[df.apply(vigente, axis=1)].sort_values("_fecha", ascending=False)
    cuotas = {f: max(1, round(60 * c / len(vig))) for f, c in vig["fuente"].value_counts().items()} if len(vig) else {}
    if len(vig):
        muestra = pd.concat([vig[vig["fuente"] == f].head(q) for f, q in cuotas.items()])
        if len(muestra) < 60:  # completar hasta 60 con las vigentes restantes más recientes
            resto = vig.loc[~vig.index.isin(muestra.index)].head(60 - len(muestra))
            muestra = pd.concat([muestra, resto])
        muestra = muestra.head(60)
    else:
        muestra = vig

    def fila_detalle(r):
        inf = habs.get("por_categoria", {}).get(r["_cat"], {})
        def split_habs(v):
            if pd.isna(v): return []
            return [h.strip() for h in str(v).split(";") if h.strip() and h.strip().lower() != "nan"]
        ht = split_habs(r.get("habilidades_tecnicas")) or inf.get("tecnicas", ["(inferida del título)"])[:2]
        hb = split_habs(r.get("habilidades_blandas")) or inf.get("blandas", [])[:2]
        sal_min = pd.to_numeric(r.get("salario_min"), errors="coerce")
        sal_max = pd.to_numeric(r.get("salario_max"), errors="coerce")
        if r["fuente"] == "Adzuna":
            if pd.notna(sal_min) and sal_min > UMBRAL_SALARIO_ANUAL: sal_min /= 12
            if pd.notna(sal_max) and sal_max > UMBRAL_SALARIO_ANUAL: sal_max /= 12
        return {"titulo": r["titulo_puesto"], "empresa": r["empresa"], "ubicacion": r["ubicacion"],
                "habilidades_tecnicas": ht, "habilidades_blandas": hb,
                "salario_min": int(sal_min) if pd.notna(sal_min) else None,
                "salario_max": int(sal_max) if pd.notna(sal_max) else None,
                "fecha_publicacion": r["_fecha"].strftime("%Y-%m-%d") if pd.notna(r["_fecha"]) else None,
                "fuente": r["fuente"], "url": r["url_original"]}

    bloque = {
        "nombre": a.sector, "vacantes_verificadas": n, "vacantes_descartadas": a.descartadas,
        "vacantes_con_habilidades": int(df["_hab_explicita"].sum()),
        "pct_con_habilidades": round(100*df["_hab_explicita"].sum()/n, 1),
        "fuentes": {k: int(v) for k, v in fuentes.items()},
        "categorias_puesto": categorias, "habilidades_tecnicas": tecnicas,
        "habilidades_blandas": blandas, "top_empresas": top_emp,
        "cobertura_geografica": {"top_estados": top_estados, "estados_unicos": int(est.size)},
        "salario": salario, "evolucion_semanal": evo,
        "total_empresas_unicas": int(df["_emp"].nunique()),
        "ultima_semana_vacantes": evo[-1]["vacantes"] if evo else 0,
        "vacantes_detalle": [fila_detalle(r) for _, r in muestra.iterrows()],
        "vacantes_detalle_nota": (
            f"Generado programáticamente ({hoy.isoformat()}) por etapa4_sector_json.py sobre "
            f"{n} vacantes verificadas ({', '.join(Path(f).name for f in a.verified)}). Todos los "
            "conteos son exactos (pandas, sin estimaciones). Salario normalizado a MXN mensual "
            f"(Adzuna >{UMBRAL_SALARIO_ANUAL:,}/año se dividió entre 12). vacantes_con_habilidades "
            "cuenta solo habilidades EXPLÍCITAS en el CSV fuente. Muestra vacantes_detalle: filtro "
            "de recencia Adzuna<=10d / Indeed<=15d / OCC<=20d desde hoy, muestreo proporcional por "
            "fuente, máx. 60; habilidades faltantes inferidas por categoría de puesto."),
    }
    Path(a.out).write_text(json.dumps(bloque, ensure_ascii=False, indent=1))
    print(f"OK -> {a.out} | verificadas={n} | detalle={len(bloque['vacantes_detalle'])} filas | "
          f"empresas_unicas={bloque['total_empresas_unicas']} | mediana={salario['mediana_mensual_mxn']}")

if __name__ == "__main__":
    main()
