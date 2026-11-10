# Vigilar tiendas

Revisa cada 15 minutos las páginas de `urls.txt` y avisa por Telegram cuando:

- 🆕 aparece un producto nuevo
- 📦 un producto agotado vuelve a tener stock
- 💸 un producto baja de precio

## Cómo funciona

El script descarga cada página, busca las **tarjetas de producto** (el recuadro con foto, nombre y precio) y de cada una saca nombre, precio, precio anterior, si está agotado, SKU y enlace. Luego lo compara con lo que vio la vez anterior, que queda guardado en la carpeta `estado/`.

Plataformas que reconoce solo:

| Plataforma | Ejemplo | Cómo lee los productos |
|---|---|---|
| Jumpseller | santogames.cl | tarjetas `.product-block` |
| WooCommerce | tiendas WordPress | tarjetas `li.product` o su API pública |
| Shopify | tiendas `/collections/...` | API pública `/products.json` |

Para otra tienda, agrega en `urls.txt` el selector CSS de la tarjeta:

```
https://otratienda.cl/pokemon | .producto-card
```

## Configuración

1. **Bot de Telegram:** crea un bot con @BotFather, mándale un mensaje y saca tu chat id en `https://api.telegram.org/bot<TOKEN>/getUpdates`.
2. **Secretos en GitHub:** *Settings → Secrets and variables → Actions → New repository secret*:
   - `TELEGRAM_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. **Tiendas:** edita `urls.txt` (una URL por línea).
4. **Probar:** *Actions → Vigilar tiendas → Run workflow*. La primera ejecución solo guarda los productos actuales; desde la segunda avisa de los cambios.

## Probar una página en tu computador

```bash
pip install -r requirements.txt
python vigilar_productos.py https://www.santogames.cl/catalogo/preventas-pokemon-tcg --probar
```

Muestra los productos que detecta, sin guardar nada ni avisar.

## Notas

- **Repo público:** minutos de Actions ilimitados. Los secretos siguen siendo privados.
- **Repo privado:** 2.000 minutos gratis al mes; cambia el cron a `*/30 * * * *`.
- Si una tienda bloquea los servidores de GitHub (error 403), revísala desde tu computador con el script local.
- Para empezar de cero con una tienda, borra su archivo en `estado/`.
