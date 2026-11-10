#!/usr/bin/env python3
"""
Vigila tiendas online y avisa por Telegram cuando:
  🆕 aparece un producto nuevo
  📦 un producto agotado vuelve a tener stock
  💸 un producto baja de precio

Cómo funciona (scraping por tarjetas):
  1. Descarga el HTML de la página.
  2. Busca cada "tarjeta" de producto (el recuadro con foto, nombre y precio).
  3. De cada tarjeta saca: nombre, precio, precio anterior, si está agotado, SKU y enlace.
  4. Compara con lo que vio la vez anterior (guardado en la carpeta de estado).

Plataformas soportadas:
  - Jumpseller (ej. santogames.cl): tarjetas .product-block
  - WooCommerce (ej. tiendas WordPress): tarjetas li.product, o su API pública
  - Shopify: API pública /products.json
  - Cualquier otra: indicando en urls.txt el selector CSS de la tarjeta → "URL | .mi-tarjeta"

Uso:
    python3 vigilar_productos.py --archivo urls.txt            # revisa cada 10 min
    python3 vigilar_productos.py --archivo urls.txt --una-vez  # una sola vez (GitHub Actions / cron)
    python3 vigilar_productos.py https://tienda.cl/categoria --probar   # muestra qué productos detecta

Telegram: define las variables de entorno TELEGRAM_TOKEN y TELEGRAM_CHAT_ID.
Requisitos: pip install requests beautifulsoup4 curl_cffi
"""

import argparse
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# curl_cffi imita la "huella" de un Chrome real; evita varios bloqueos 403
try:
    from curl_cffi import requests as navegador
    SESION = navegador.Session(impersonate="chrome")
except ImportError:
    SESION = requests.Session()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-CL,es;q=0.9,en;q=0.8",
}


# =====================================================================
#  PERFILES: dónde está cada dato dentro de la tarjeta, según la plataforma.
#  Para agregar una tienda nueva basta con agregar un perfil aquí.
# =====================================================================
PERFILES = {
    "jumpseller": {
        "tarjeta":      ".product-block",
        "nombre":       ".product-block__name",
        "enlace":       "a.product-block__name, a.product-block__anchor",
        "precio":       ".product-block__price--new, .product-block__price",
        "precio_antes": ".product-block__price--old",
        "agotado":      ".product-block__label--status",   # "Agotado" / "No disponible"
        "sku":          ".product-block__sku",
    },
    "woocommerce": {
        "tarjeta":      "li.product, div.product.type-product",
        "nombre":       ".woocommerce-loop-product__title, h2, h3",
        "enlace":       "a.woocommerce-LoopProduct-link, a",
        "precio":       ".price ins .amount, .price .amount",
        "precio_antes": ".price del .amount",
        "agotado":      ".out-of-stock, .outofstock, .stock.out-of-stock",
        "sku":          None,
    },
}


# ------------------------------------------------------------ utilidades

def descargar(url, **kw):
    kw.setdefault("timeout", 25)
    return SESION.get(url, headers=HEADERS, **kw)


def a_numero(texto):
    """'$149,990 CLP' → 149990   |   '$ 54.990' → 54990"""
    if not texto:
        return None
    texto = re.sub(r"[.,]\d{2}(?=\D*$)", "", texto)  # quitar decimales tipo ",00" o ".99"
    digitos = re.sub(r"\D", "", texto)
    return int(digitos) if digitos else None


def formato_precio(n):
    return f"${n:,.0f}".replace(",", ".") if n else "$?"


def texto_de(tarjeta, selector):
    if not selector:
        return ""
    el = tarjeta.select_one(selector)
    return el.get_text(" ", strip=True) if el else ""


def producto(nombre, url, precio=None, precio_antes=None, agotado=False, sku=""):
    return {"nombre": nombre.strip(), "url": url, "precio": precio,
            "precio_antes": precio_antes, "agotado": agotado, "sku": sku}


# ------------------------------------------------------------ extractores

def extraer_tarjetas(html, url, perfil):
    """Recorre cada tarjeta de producto y saca sus datos según el perfil."""
    sopa = BeautifulSoup(html, "html.parser")
    productos = {}
    for tarjeta in sopa.select(perfil["tarjeta"]):
        enlace_el = tarjeta.select_one(perfil["enlace"]) or tarjeta.find("a", href=True)
        if not enlace_el or not enlace_el.get("href"):
            continue
        enlace = urljoin(url, enlace_el["href"]).split("?")[0].split("#")[0]
        nombre = texto_de(tarjeta, perfil["nombre"]) or enlace_el.get("title", "") or enlace
        estado = texto_de(tarjeta, perfil["agotado"]).lower()
        agotado = bool(perfil["agotado"] and tarjeta.select_one(perfil["agotado"])) and (
            not estado or any(p in estado for p in ("agotado", "no disponible", "sin stock", "out of stock")))
        # En WooCommerce la propia tarjeta lleva la clase "outofstock"
        agotado = agotado or bool({"outofstock", "out-of-stock"} & set(tarjeta.get("class", [])))
        productos[enlace] = producto(
            nombre, enlace,
            precio=a_numero(texto_de(tarjeta, perfil["precio"])),
            precio_antes=a_numero(texto_de(tarjeta, perfil["precio_antes"])),
            agotado=agotado,
            sku=texto_de(tarjeta, perfil["sku"]),
        )
    return productos


def extraer_con_selector(html, url, selector):
    """Para tiendas sin perfil: el usuario indica el selector de la tarjeta."""
    perfil = {"tarjeta": selector, "nombre": "h1, h2, h3, h4, [class*=name], [class*=title]",
              "enlace": "a[href]", "precio": "[class*=price]", "precio_antes": "del, s",
              "agotado": "[class*=agotado], [class*=out-of-stock], [class*=sold-out]", "sku": None}
    return extraer_tarjetas(html, url, perfil)


def extraer_shopify(url):
    p = urlparse(url)
    ruta = p.path.rstrip("/")
    api = (f"{p.scheme}://{p.netloc}{ruta}/products.json" if "/collections/" in ruta
           else f"{p.scheme}://{p.netloc}/products.json")
    try:
        r = descargar(api, params={"limit": 250})
        if r.status_code != 200 or "json" not in r.headers.get("Content-Type", ""):
            return None
        productos = {}
        for prod in r.json().get("products", []):
            variantes = prod.get("variants") or [{}]
            enlace = f"{p.scheme}://{p.netloc}/products/{prod['handle']}"
            productos[enlace] = producto(
                prod["title"], enlace,
                precio=a_numero(str(variantes[0].get("price", "")).split(".")[0]),
                precio_antes=a_numero(str(variantes[0].get("compare_at_price") or "").split(".")[0]),
                agotado=not any(v.get("available") for v in variantes),
                sku=variantes[0].get("sku") or "")
        return productos
    except Exception:
        return None


def extraer_woocommerce_api(url):
    """API pública de WooCommerce; sirve cuando la página HTML está bloqueada."""
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}/wp-json/wc/store/v1"
    params = {"per_page": 100, "orderby": "date", "order": "desc"}
    try:
        m = re.search(r"/(?:categoria-producto|product-category)/(?:.*/)?([^/]+)/?$", p.path)
        if m:
            r = descargar(f"{base}/products/categories", params={"per_page": 100})
            ids = [str(c["id"]) for c in r.json() if c.get("slug") == m.group(1)]
            if not ids:
                return None
            params["category"] = ids[0]
        r = descargar(f"{base}/products", params=params)
        if r.status_code != 200:
            return None
        productos = {}
        for prod in r.json():
            pr = prod.get("prices") or {}
            div = 10 ** int(pr.get("currency_minor_unit") or 0)
            precio = int(pr["price"]) // div if pr.get("price") else None
            antes = int(pr["regular_price"]) // div if pr.get("regular_price") else None
            nombre = BeautifulSoup(prod.get("name", ""), "html.parser").get_text()
            productos[prod["permalink"]] = producto(
                nombre, prod["permalink"], precio=precio,
                precio_antes=antes if antes and precio and antes > precio else None,
                agotado=not prod.get("is_in_stock", True), sku=prod.get("sku", ""))
        return productos
    except Exception:
        return None


def obtener_productos(url, selector=None):
    """Elige el mejor método para la página y devuelve (productos, metodo)."""
    if selector:
        r = descargar(url)
        r.raise_for_status()
        return extraer_con_selector(r.text, url, selector), "selector"

    productos = extraer_shopify(url)
    if productos:
        return productos, "shopify"

    try:
        r = descargar(url)
        r.raise_for_status()
    except Exception:
        # Página bloqueada (ej. 403): intentar API de WooCommerce
        productos = extraer_woocommerce_api(url)
        if productos is not None:
            return productos, "woocommerce-api"
        raise

    for nombre, perfil in PERFILES.items():
        productos = extraer_tarjetas(r.text, url, perfil)
        if productos:
            return productos, nombre
    productos = extraer_woocommerce_api(url)
    if productos:
        return productos, "woocommerce-api"
    return {}, "ninguno"


# ------------------------------------------------------------ avisos

def describir(p):
    linea = f"• {p['nombre']} — {formato_precio(p['precio'])}"
    if p.get("precio_antes") and p.get("precio") and p["precio_antes"] > p["precio"]:
        linea += f" (antes {formato_precio(p['precio_antes'])})"
    if p.get("agotado"):
        linea += " [AGOTADO]"
    return f"{linea}\n  {p['url']}"


def avisar(titulo, productos):
    mensaje = "\n\n".join(describir(p) for p in productos)
    print(f"\n🔔 {titulo}\n{mensaje}\n", flush=True)
    token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if token and chat:
        try:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              data={"chat_id": chat, "text": f"{titulo}\n\n{mensaje}"[:4000],
                                    "disable_web_page_preview": True}, timeout=15)
            if not r.ok:
                print(f"(Telegram respondió {r.status_code}: {r.text[:200]})")
        except requests.RequestException as e:
            print(f"(No se pudo enviar a Telegram: {e})")


# ------------------------------------------------------------ estado

def archivo_estado(url):
    nombre = re.sub(r"[^a-zA-Z0-9]+", "_", url)[:80]
    carpeta = Path(os.getenv("VIGILAR_ESTADO", Path.home() / ".vigilar_productos"))
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta / f"{nombre}.json"


def cargar(ruta):
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        productos = datos.get("productos") or {}
        # Estados de versiones anteriores del script (texto en vez de datos) → empezar de cero
        if any(not isinstance(v, dict) for v in productos.values()):
            return None
        return productos or None
    except (FileNotFoundError, ValueError, AttributeError):
        return None


# ------------------------------------------------------------ revisión

def revisar(url, selector):
    hora = datetime.now().strftime("%H:%M:%S")
    p = urlparse(url)
    sitio = p.netloc.replace("www.", "")
    etiqueta = f"{sitio}/{p.path.strip('/')}"
    ruta = archivo_estado(url)
    anteriores = cargar(ruta)

    actuales, metodo = obtener_productos(url, selector)

    if not actuales:
        print(f"[{hora}] ⚠️  {etiqueta}: no detecté productos. "
              "Agrega en urls.txt el selector de la tarjeta: URL | .clase-de-la-tarjeta")
        return

    if anteriores is None:
        print(f"[{hora}] {etiqueta}: primera revisión ({metodo}), "
              f"{len(actuales)} productos guardados.")
    else:
        nuevos = [v for k, v in actuales.items() if k not in anteriores]
        repuestos = [v for k, v in actuales.items()
                     if k in anteriores and anteriores[k].get("agotado") and not v["agotado"]]
        rebajas = [v for k, v in actuales.items()
                   if k in anteriores and v["precio"] and anteriores[k].get("precio")
                   and v["precio"] < anteriores[k]["precio"]]
        if nuevos:
            avisar(f"🆕 {sitio}: {len(nuevos)} producto(s) nuevo(s)", nuevos)
        if repuestos:
            avisar(f"📦 {sitio}: {len(repuestos)} producto(s) volvieron a tener stock", repuestos)
        if rebajas:
            avisar(f"💸 {sitio}: {len(rebajas)} producto(s) bajaron de precio",
                   [dict(v, precio_antes=anteriores[v["url"]]["precio"]) for v in rebajas])
        if not (nuevos or repuestos or rebajas):
            print(f"[{hora}] {etiqueta}: sin novedades ({len(actuales)} productos, {metodo}).")

    ruta.write_text(json.dumps({"url": url, "metodo": metodo, "productos": actuales},
                               ensure_ascii=False, indent=1), encoding="utf-8")


def probar(url, selector):
    """Muestra en pantalla lo que se detecta, sin guardar nada ni avisar."""
    productos, metodo = obtener_productos(url, selector)
    print(f"{url}\nMétodo: {metodo} — {len(productos)} productos\n")
    for p in productos.values():
        print(describir(p) + (f"\n  SKU: {p['sku']}" if p.get("sku") else ""))


# ------------------------------------------------------------ programa principal

def leer_archivo(ruta):
    """Una URL por línea; opcional 'URL | selector de tarjeta'. Ignora vacías y #comentarios."""
    objetivos = []
    for linea in Path(ruta).read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#"):
            url, _, selector = linea.partition("|")
            objetivos.append((url.strip(), selector.strip() or None))
    return objetivos


def main():
    ap = argparse.ArgumentParser(description="Avisa cuando tiendas online suben productos nuevos.")
    ap.add_argument("urls", nargs="*", help="Páginas a vigilar")
    ap.add_argument("-a", "--archivo", help="Archivo con una URL por línea (opcional 'URL | selector')")
    ap.add_argument("--selector", help="Selector CSS de la tarjeta para las URLs de la línea de comandos")
    ap.add_argument("--intervalo", type=int, default=600, help="Segundos entre vueltas (por defecto 600)")
    ap.add_argument("--una-vez", action="store_true", help="Revisar una sola vez y salir")
    ap.add_argument("--probar", action="store_true", help="Solo mostrar los productos detectados")
    args = ap.parse_args()

    objetivos = [(u, args.selector) for u in args.urls]
    if args.archivo:
        objetivos += leer_archivo(args.archivo)
    if not objetivos:
        ap.error("Indica al menos una URL o un archivo con --archivo urls.txt")

    if args.probar:
        for url, selector in objetivos:
            try:
                probar(url, selector)
            except Exception as e:
                print(f"Error con {url} → {e}")
            print("-" * 60)
        return

    print(f"Vigilando {len(objetivos)} página(s).")
    while True:
        for i, (url, selector) in enumerate(objetivos):
            try:
                revisar(url, selector)
            except Exception as e:
                print(f"[{datetime.now():%H:%M:%S}] Error con {url} → {e}")
            if i < len(objetivos) - 1:
                time.sleep(3)
        if args.una_vez:
            break
        time.sleep(max(60, args.intervalo))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDetenido.")
