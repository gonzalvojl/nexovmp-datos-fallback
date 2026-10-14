#!/usr/bin/env python3
"""Replica validada del servidor primario NexoVMP hacia GitHub Pages."""
from __future__ import annotations
import hashlib,json,os,shutil,sys,urllib.error,urllib.parse,urllib.request
from pathlib import Path

PRIMARY_BASE=os.environ.get("NEXOVMP_PRIMARY_BASE","https://nexovmp-datos.pages.dev/")
TIMEOUT=25
USER_AGENT="NexoVMP-Fallback-Mirror/2.0"
SUPPORTED_SCHEMA=1
ROOT=Path(__file__).resolve().parents[1]
SITE=ROOT/"site"

class MirrorError(RuntimeError): pass

def fetch(url):
    request=urllib.request.Request(url,headers={"Accept":"application/json","User-Agent":USER_AGENT,"Cache-Control":"no-cache"})
    try:
        with urllib.request.urlopen(request,timeout=TIMEOUT) as response:
            if response.status<200 or response.status>=300: raise MirrorError(f"HTTP {response.status}: {url}")
            data=response.read()
    except (urllib.error.URLError,TimeoutError) as exc:
        raise MirrorError(f"No se pudo descargar {url}: {exc}") from exc
    if not data: raise MirrorError(f"Respuesta vacía: {url}")
    return data

def parse_json(data,label):
    try: value=json.loads(data.decode("utf-8"))
    except Exception as exc: raise MirrorError(f"JSON no válido en {label}: {exc}") from exc
    if not isinstance(value,dict): raise MirrorError(f"Raíz JSON no válida en {label}")
    return value

def sha256(data): return hashlib.sha256(data).hexdigest()
def require(condition,message):
    if not condition: raise MirrorError(message)
def write_bytes(path,data):
    destination=SITE/path
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_bytes(data)

def mirror_catalog():
    manifest_url=urllib.parse.urljoin(PRIMARY_BASE,"manifest.json")
    manifest_data=fetch(manifest_url); manifest=parse_json(manifest_data,"manifest.json")
    require(manifest.get("schemaVersion")==SUPPORTED_SCHEMA,"Schema catálogo no compatible")
    require(int(manifest.get("catalogVersion",0))>0,"catalogVersion no válido")
    require(int(manifest.get("vehicleCount",0))>0,"vehicleCount no válido")
    require(int(manifest.get("certificateCount",0))>0,"certificateCount no válido")
    expected=str(manifest.get("sha256","")).lower(); require(len(expected)==64,"SHA catálogo no válido")
    catalog_url=urllib.parse.urljoin(manifest_url,str(manifest.get("catalogURL","")))
    catalog_data=fetch(catalog_url); require(sha256(catalog_data)==expected,"SHA catálogo no coincide")
    catalog=parse_json(catalog_data,"nexovmp_catalog.json")
    require(catalog.get("schemaVersion")==manifest["schemaVersion"],"Schema catálogo/manifiesto no coincide")
    require(catalog.get("catalogVersion")==manifest["catalogVersion"],"Versión catálogo/manifiesto no coincide")
    vehicles=catalog.get("vehicles"); require(isinstance(vehicles,list),"vehicles no es lista")
    require(len(vehicles)==int(manifest["vehicleCount"]),"Recuento vehículos no coincide")
    certificates=set()
    for vehicle in vehicles:
        require(isinstance(vehicle,dict),"Entrada VMP no válida")
        for raw in str(vehicle.get("certificate","")).replace(";",",").split(","):
            code=raw.strip().upper()
            if code:
                require(code not in certificates,f"Certificado duplicado: {code}")
                certificates.add(code)
    require(len(certificates)==int(manifest["certificateCount"]),"Recuento certificados no coincide")
    write_bytes("manifest.json",manifest_data); write_bytes("nexovmp_catalog.json",catalog_data)
    return manifest

def mirror_national():
    manifest_url=urllib.parse.urljoin(PRIMARY_BASE,"national/manifest.json")
    manifest_data=fetch(manifest_url); manifest=parse_json(manifest_data,"national/manifest.json")
    require(manifest.get("schemaVersion")==SUPPORTED_SCHEMA,"Schema estatal no compatible")
    require(manifest.get("jurisdiction")=="ES","Jurisdicción estatal no válida")
    require(manifest.get("scope")=="national","Scope estatal no válido")
    require(manifest.get("status") in {"verified","review_required"},"Estado estatal no válido")
    require(int(manifest.get("datasetVersion",0))>0,"datasetVersion estatal no válido")
    expected=str(manifest.get("sha256","")).lower(); require(len(expected)==64,"SHA estatal no válido")
    rules_path=str(manifest.get("rulesURL","")).strip(); require(rules_path,"Falta rulesURL estatal")
    rules_url=urllib.parse.urljoin(manifest_url,rules_path)
    rules_data=fetch(rules_url); require(sha256(rules_data)==expected,"SHA estatal no coincide")
    rules=parse_json(rules_data,"national/rules.json")
    require(rules.get("schemaVersion")==SUPPORTED_SCHEMA,"Schema rules estatal no compatible")
    require(rules.get("jurisdiction")=="ES" and rules.get("scope")=="national","Ámbito estatal inválido")
    items=rules.get("rules"); require(isinstance(items,list) and items,"rules estatal inválido")
    require(len(items)==int(manifest.get("ruleCount",-1)),"ruleCount estatal no coincide")
    write_bytes("national/manifest.json",manifest_data); write_bytes("national/rules.json",rules_data)
    return manifest

def mirror_municipal():
    manifest_url=urllib.parse.urljoin(PRIMARY_BASE,"municipal/manifest.json")
    manifest_data=fetch(manifest_url); manifest=parse_json(manifest_data,"municipal/manifest.json")
    require(manifest.get("schemaVersion")==SUPPORTED_SCHEMA,"Schema municipal no compatible")
    require(int(manifest.get("datasetVersion",0))>0,"datasetVersion municipal no válido")
    entries=manifest.get("municipalities"); require(isinstance(entries,list),"municipalities no es lista")
    seen=set()
    for entry in entries:
        require(isinstance(entry,dict),"Entrada municipal no válida")
        code="".join(ch for ch in str(entry.get("ineCode","")) if ch.isdigit())[-5:]
        require(len(code)==5,f"Código INE no válido: {entry.get('ineCode')}")
        require(code not in seen,f"Código INE duplicado: {code}"); seen.add(code)
        if str(entry.get("status","")).lower()!="verified": continue
        profile_path=str(entry.get("profilePath","")).strip()
        expected=str(entry.get("sha256","")).lower()
        require(profile_path,f"Falta profilePath {code}"); require(len(expected)==64,f"SHA municipal no válido {code}")
        profile_url=urllib.parse.urljoin(manifest_url,profile_path)
        data=fetch(profile_url); require(sha256(data)==expected,f"SHA municipal no coincide {code}")
        profile=parse_json(data,f"municipio {code}")
        pcode="".join(ch for ch in str(profile.get("ineCode","")) if ch.isdigit())[-5:]
        require(profile.get("schemaVersion")==SUPPORTED_SCHEMA,f"Schema ficha no válido {code}")
        require(pcode==code,f"Código INE ficha no coincide {code}")
        require(bool(str(profile.get("municipality","")).strip()),f"Municipio vacío {code}")
        require(bool(str(profile.get("verifiedAt","")).strip()),f"verifiedAt vacío {code}")
        clean=profile_path.lstrip("/")
        if clean.startswith("municipal/"): clean=clean[len("municipal/"):]
        write_bytes(f"municipal/{clean}",data)
    write_bytes("municipal/manifest.json",manifest_data)
    return manifest

def write_index(catalog,national,municipal):
    body=f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoVMP · servidor secundario</title></head><body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:760px;margin:60px auto;padding:0 24px;color:#101418"><h1>NexoVMP</h1><p>Servidor secundario de datos verificados.</p><ul><li>Catálogo: {catalog.get('catalogVersion')}</li><li>VMP: {catalog.get('vehicleCount')}</li><li>Certificados: {catalog.get('certificateCount')}</li><li>Normativa estatal: {national.get('datasetVersion')} · {national.get('status')}</li><li>Municipios publicados: {len(municipal.get('municipalities',[]))}</li></ul><p>La aplicación valida esquema y SHA-256 antes de aceptar los datos.</p></body></html>"""
    write_bytes("index.html",body.encode("utf-8")); write_bytes(".nojekyll",b"")

def main():
    if SITE.exists(): shutil.rmtree(SITE)
    SITE.mkdir(parents=True,exist_ok=True)
    catalog=mirror_catalog(); national=mirror_national(); municipal=mirror_municipal()
    write_index(catalog,national,municipal)
    print(f"Mirror válido: catálogo {catalog.get('catalogVersion')} · estatal {national.get('datasetVersion')} ({national.get('status')}) · {len(municipal.get('municipalities',[]))} municipios")
    return 0

if __name__=="__main__":
    try: raise SystemExit(main())
    except MirrorError as exc:
        print(f"ERROR: {exc}",file=sys.stderr); raise SystemExit(1)
