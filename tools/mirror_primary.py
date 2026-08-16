#!/usr/bin/env python3
"""Replica validada del servidor primario NexoVMP hacia GitHub Pages.

Principios:
- El primario sigue siendo Cloudflare Pages.
- Este mirror nunca publica datos si una validación falla.
- Si el primario no responde, el workflow falla y GitHub Pages conserva
  el último despliegue correcto.
- Solo se copian JSON ya publicados/validados por NexoVMP.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PRIMARY_BASE = os.environ.get("NEXOVMP_PRIMARY_BASE", "https://nexovmp-datos.pages.dev/")
TIMEOUT = 25
USER_AGENT = "NexoVMP-Fallback-Mirror/1.0"
SUPPORTED_SCHEMA = 1

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


class MirrorError(RuntimeError):
    pass


def fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            if response.status < 200 or response.status >= 300:
                raise MirrorError(f"HTTP {response.status}: {url}")
            data = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise MirrorError(f"No se pudo descargar {url}: {exc}") from exc
    if not data:
        raise MirrorError(f"Respuesta vacía: {url}")
    return data


def parse_json(data: bytes, label: str) -> dict:
    try:
        value = json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise MirrorError(f"JSON no válido en {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise MirrorError(f"Raíz JSON no válida en {label}")
    return value


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise MirrorError(message)


def write_bytes(relative_path: str, data: bytes) -> None:
    destination = SITE / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def mirror_catalog() -> tuple[dict, dict]:
    manifest_url = urllib.parse.urljoin(PRIMARY_BASE, "manifest.json")
    manifest_data = fetch(manifest_url)
    manifest = parse_json(manifest_data, "manifest.json")

    require(manifest.get("schemaVersion") == SUPPORTED_SCHEMA, "Schema de catálogo no compatible")
    require(int(manifest.get("catalogVersion", 0)) > 0, "catalogVersion no válido")
    require(int(manifest.get("vehicleCount", 0)) > 0, "vehicleCount no válido")
    require(int(manifest.get("certificateCount", 0)) > 0, "certificateCount no válido")
    expected_hash = str(manifest.get("sha256", "")).lower()
    require(len(expected_hash) == 64, "SHA-256 de catálogo no válido")

    catalog_url = urllib.parse.urljoin(manifest_url, str(manifest.get("catalogURL", "")))
    catalog_data = fetch(catalog_url)
    require(sha256(catalog_data) == expected_hash, "SHA-256 del catálogo no coincide")
    catalog = parse_json(catalog_data, "nexovmp_catalog.json")

    require(catalog.get("schemaVersion") == manifest["schemaVersion"], "Schema catálogo/manifiesto no coincide")
    require(catalog.get("catalogVersion") == manifest["catalogVersion"], "Versión catálogo/manifiesto no coincide")
    vehicles = catalog.get("vehicles")
    require(isinstance(vehicles, list), "vehicles no es una lista")
    require(len(vehicles) == int(manifest["vehicleCount"]), "Recuento de vehículos no coincide")

    certificates: set[str] = set()
    for vehicle in vehicles:
        require(isinstance(vehicle, dict), "Entrada VMP no válida")
        for raw in str(vehicle.get("certificate", "")).replace(";", ",").split(","):
            code = raw.strip().upper()
            if code:
                require(code not in certificates, f"Certificado duplicado: {code}")
                certificates.add(code)
    require(len(certificates) == int(manifest["certificateCount"]), "Recuento de certificados no coincide")

    write_bytes("manifest.json", manifest_data)
    write_bytes("nexovmp_catalog.json", catalog_data)
    return manifest, catalog


def mirror_municipal() -> dict:
    manifest_url = urllib.parse.urljoin(PRIMARY_BASE, "municipal/manifest.json")
    manifest_data = fetch(manifest_url)
    manifest = parse_json(manifest_data, "municipal/manifest.json")

    require(manifest.get("schemaVersion") == SUPPORTED_SCHEMA, "Schema municipal no compatible")
    require(int(manifest.get("datasetVersion", 0)) > 0, "datasetVersion municipal no válido")
    entries = manifest.get("municipalities")
    require(isinstance(entries, list), "municipalities no es una lista")

    seen: set[str] = set()
    for entry in entries:
        require(isinstance(entry, dict), "Entrada municipal no válida")
        code = "".join(ch for ch in str(entry.get("ineCode", "")) if ch.isdigit())[-5:]
        require(len(code) == 5, f"Código INE no válido: {entry.get('ineCode')}")
        require(code not in seen, f"Código INE duplicado: {code}")
        seen.add(code)

        status = str(entry.get("status", "")).strip().lower()
        if status != "verified":
            continue

        profile_path = str(entry.get("profilePath", "")).strip()
        expected_hash = str(entry.get("sha256", "")).strip().lower()
        require(profile_path, f"Falta profilePath para {code}")
        require(len(expected_hash) == 64, f"SHA-256 municipal no válido para {code}")

        profile_url = urllib.parse.urljoin(manifest_url, profile_path)
        profile_data = fetch(profile_url)
        require(sha256(profile_data) == expected_hash, f"SHA-256 municipal no coincide para {code}")
        profile = parse_json(profile_data, f"municipio {code}")
        profile_code = "".join(ch for ch in str(profile.get("ineCode", "")) if ch.isdigit())[-5:]
        require(profile.get("schemaVersion") == SUPPORTED_SCHEMA, f"Schema de ficha no válido para {code}")
        require(profile_code == code, f"Código INE de ficha no coincide: {code}")
        require(bool(str(profile.get("municipality", "")).strip()), f"Municipio vacío en {code}")
        require(bool(str(profile.get("verifiedAt", "")).strip()), f"verifiedAt vacío en {code}")

        # Respetamos la misma ruta declarada por el manifiesto.
        clean_path = profile_path.lstrip("/")
        if clean_path.startswith("municipal/"):
            clean_path = clean_path[len("municipal/"):]
        write_bytes(f"municipal/{clean_path}", profile_data)

    write_bytes("municipal/manifest.json", manifest_data)
    return manifest


def write_index(catalog_manifest: dict, municipal_manifest: dict) -> None:
    html = f"""<!doctype html>
<html lang=\"es\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>NexoVMP · servidor secundario</title></head>
<body style=\"font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:760px;margin:60px auto;padding:0 24px;color:#101418\">
<h1>NexoVMP</h1><p>Servidor secundario de datos verificados.</p>
<ul><li>Catálogo: {catalog_manifest.get('catalogVersion')}</li><li>VMP: {catalog_manifest.get('vehicleCount')}</li><li>Certificados: {catalog_manifest.get('certificateCount')}</li><li>Municipios publicados: {len(municipal_manifest.get('municipalities', []))}</li></ul>
<p>La aplicación valida esquema y SHA-256 antes de aceptar los datos.</p>
</body></html>"""
    write_bytes("index.html", html.encode("utf-8"))
    write_bytes(".nojekyll", b"")


def main() -> int:
    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir(parents=True, exist_ok=True)

    catalog_manifest, _catalog = mirror_catalog()
    municipal_manifest = mirror_municipal()
    write_index(catalog_manifest, municipal_manifest)

    print(
        "Mirror válido: "
        f"catálogo {catalog_manifest.get('catalogVersion')} · "
        f"{catalog_manifest.get('vehicleCount')} VMP · "
        f"{len(municipal_manifest.get('municipalities', []))} municipios"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MirrorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
