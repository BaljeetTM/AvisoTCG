#!/usr/bin/env python3
"""
Vigila una tienda online y avisa cuando aparecen productos nuevos.

Uso:
    python3 vigilar_productos.py https://tienda.cl/collections/pokemon
    python3 vigilar_productos.py https://tienda1.cl/pokemon https://tienda2.cl/novedades
    python3 vigilar_productos.py --archivo urls.txt --intervalo 300

Archivo urls.txt (una por línea; el selector CSS es opcional):
    # Tiendas Pokémon
    https://tienda1.cl/categoria/pokemon
    https://tienda2.cl/novedades | .product-card a

Cómo detecta productos (en este orden):
  1. Tiendas Shopify: usa /products.json (lo más confiable).
  2. Si pasas --selector: toma los elementos que calcen con ese selector CSS.
  3. Si no: busca enlaces que parezcan de producto (/products/, /producto/, /product/, /p/).
  4. Último recurso: compara un hash del texto de la página (avisa de "cambios", no de productos).

Avisos: consola + notificación de escritorio (notify-send en Linux).
Opcional Telegram: define TELEGRAM_TOKEN y TELEGRAM_CHAT_ID como variables de entorno.

Requisitos: pip install requests beautifulsoup4
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}
PATRON_PRODUCTO = re.compile(r"/(products?|producto|productos|p)/[^/?#]+", re.I)


# ---------------------------------------------------------------- extracción

def productos_shopify(url):
    """Devuelve {id: titulo} si la tienda es Shopify, o None."""
    p = urlparse(url)
    ruta = p.path.rstrip("/")
    # Si es una colección, usa sus productos; si no, los de toda la tienda
    if "/collections/" in ruta:
        api = f"{p.scheme}://{p.netloc}{ruta}/products.json"
    else:
        api = f"{p.scheme}://{p.netloc}/products.json"
    productos = {}
    try:
        for pagina in range(1, 6):  # hasta 1250 productos
            r = requests.get(api, params={"limit": 250, "page": pagina},
                             headers=HEADERS, timeout=20)
            if r.status_code != 200 or "json" not in r.headers.get("Content-Type", ""):
                return None
            lote = r.json().get("products", [])
            for prod in lote:
                disponible = any(v.get("available") for v in prod.get("variants", []))
                precio = (prod.get("variants") or [{}])[0].get("price", "?")
                url_prod = f"{p.scheme}://{p.netloc}/products/{prod['handle']}"
                productos[str(prod["id"])] = (
                    f"{prod['title']} — ${precio}"
                    f"{'' if disponible else ' (agotado)'}\n   {url_prod}"
                )
            if len(lote) < 250:
                break
        return productos
    except (requests.RequestException, ValueError):
        return None


def productos_html(url, selector=None):
    """Devuelve ({clave: texto}, hash_pagina) a partir del HTML."""
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    sopa = BeautifulSoup(r.text, "html.parser")
    productos = {}

    if selector:
        for el in sopa.select(selector):
            texto = el.get_text(" ", strip=True)
            href = el.get("href") or (el.find("a") or {}).get("href")
            enlace = urljoin(url, href) if href else ""
            clave = enlace or texto
            if clave:
                productos[clave] = f"{texto}\n   {enlace}".rstrip()
    else:
        for a in sopa.find_all("a", href=True):
            enlace = urljoin(url, a["href"]).split("#")[0].split("?")[0]
            if PATRON_PRODUCTO.search(urlparse(enlace).path):
                texto = a.get_text(" ", strip=True) or a.get("title", "")
                # Si el mismo enlace aparece varias veces, nos quedamos con el texto más largo
                if len(texto) > len(productos.get(enlace, "").split("\n")[0]):
                    productos[enlace] = f"{texto}\n   {enlace}"
                productos.setdefault(enlace, enlace)

    for basura in sopa(["script", "style", "noscript"]):
        basura.decompose()
    hash_pagina = hashlib.sha256(sopa.get_text(" ", strip=True).encode()).hexdigest()
    return productos, hash_pagina


# ---------------------------------------------------------------- avisos

def avisar(titulo, mensaje):
    print(f"\n🔔 {titulo}\n{mensaje}\n", flush=True)
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-u", "critical", titulo, mensaje[:500]],
                       check=False)
    token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if token and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": f"{titulo}\n\n{mensaje}"[:4000],
                                "disable_web_page_preview": True}, timeout=15)
        except requests.RequestException as e:
            print(f"(No se pudo enviar a Telegram: {e})")


# ---------------------------------------------------------------- estado

def archivo_estado(url):
    nombre = re.sub(r"[^a-zA-Z0-9]+", "_", url)[:80]
    # En GitHub Actions se usa la carpeta "estado" del repo (variable VIGILAR_ESTADO)
    carpeta = Path(os.getenv("VIGILAR_ESTADO", Path.home() / ".vigilar_productos"))
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta / f"{nombre}.json"


def cargar(ruta):
    try:
        return json.loads(ruta.read_text())
    except (FileNotFoundError, ValueError):
        return None


# ---------------------------------------------------------------- bucle

def revisar(url, selector, estado_ruta):
    ahora = datetime.now().strftime("%H:%M:%S")
    sitio = urlparse(url).netloc.replace("www.", "")
    ruta = urlparse(url).path.strip("/") or "inicio"
    etiqueta = f"{sitio} /{ruta}"
    anterior = cargar(estado_ruta)

    productos, modo, hash_pagina = None, None, None
    if not selector:
        productos = productos_shopify(url)
        modo = "shopify" if productos is not None else None
    if productos is None:
        productos, hash_pagina = productos_html(url, selector)
        modo = "selector" if selector else ("enlaces" if productos else "hash")

    nuevo_estado = {"modo": modo, "productos": productos, "hash": hash_pagina}

    if anterior is None:
        print(f"[{ahora}] {etiqueta}: primera revisión ({modo}), {len(productos)} productos guardados.")
    elif modo == "hash":
        if hash_pagina != anterior.get("hash"):
            avisar(f"{sitio}: la página cambió", url)
        else:
            print(f"[{ahora}] {etiqueta}: sin cambios.")
    else:
        viejos = anterior.get("productos", {})
        nuevos = {k: v for k, v in productos.items() if k not in viejos}
        # En Shopify, avisa también si algo agotado vuelve a tener stock
        repuestos = {k: v for k, v in productos.items()
                     if k in viejos and "(agotado)" in viejos[k] and "(agotado)" not in v}
        if nuevos:
            avisar(f"🆕 {sitio}: {len(nuevos)} producto(s) nuevo(s)",
                   "\n".join(f"• {v}" for v in nuevos.values()))
        if repuestos:
            avisar(f"📦 {sitio}: {len(repuestos)} producto(s) volvieron a tener stock",
                   "\n".join(f"• {v}" for v in repuestos.values()))
        if not nuevos and not repuestos:
            print(f"[{ahora}] {etiqueta}: sin novedades ({len(productos)} productos).")

    estado_ruta.write_text(json.dumps(nuevo_estado, ensure_ascii=False, indent=1))


def leer_archivo(ruta):
    """Lee un archivo con una URL por línea. Formato: URL  o  URL | selector CSS.
    Las líneas vacías y las que empiezan con # se ignoran."""
    objetivos = []
    for linea in Path(ruta).read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        url, _, selector = linea.partition("|")
        objetivos.append((url.strip(), selector.strip() or None))
    return objetivos


def main():
    ap = argparse.ArgumentParser(description="Avisa cuando una o varias tiendas suben productos nuevos.")
    ap.add_argument("urls", nargs="*", help="Una o más páginas a vigilar")
    ap.add_argument("-a", "--archivo",
                    help="Archivo con una URL por línea (opcional: 'URL | selector')")
    ap.add_argument("--intervalo", type=int, default=600,
                    help="Segundos entre vueltas completas (por defecto 600 = 10 min)")
    ap.add_argument("--selector",
                    help="Selector CSS para las URLs pasadas por línea de comandos")
    ap.add_argument("--una-vez", action="store_true",
                    help="Revisar todo una sola vez y salir (útil con cron)")
    ap.add_argument("--reiniciar", action="store_true",
                    help="Borrar lo guardado y empezar de cero")
    args = ap.parse_args()

    objetivos = [(u, args.selector) for u in args.urls]
    if args.archivo:
        objetivos += leer_archivo(args.archivo)
    if not objetivos:
        ap.error("Indica al menos una URL o un archivo con --archivo urls.txt")

    if args.reiniciar:
        for url, _ in objetivos:
            archivo_estado(url).unlink(missing_ok=True)

    print(f"Vigilando {len(objetivos)} página(s). Estado en {archivo_estado(objetivos[0][0]).parent}")
    while True:
        for n, (url, selector) in enumerate(objetivos):
            try:
                revisar(url, selector, archivo_estado(url))
            except requests.RequestException as e:
                print(f"[{datetime.now():%H:%M:%S}] Error con {url}: {e}")
            except Exception as e:  # una página rota no detiene a las demás
                print(f"[{datetime.now():%H:%M:%S}] Error inesperado con {url}: {e}")
            if n < len(objetivos) - 1:
                time.sleep(3)  # pequeña pausa entre páginas
        if args.una_vez:
            break
        time.sleep(max(60, args.intervalo))  # mínimo 1 min, para no saturar las tiendas


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nDetenido.")
