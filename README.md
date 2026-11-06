# Vigilar tiendas

Revisa cada 15 minutos las páginas de `urls.txt` y avisa por Telegram cuando aparecen productos nuevos o vuelven a tener stock.

## Configuración

### 1. Crear el bot de Telegram
1. En Telegram, abre **@BotFather**, envía `/newbot` y sigue los pasos. Al final te da un **token** (ej. `123456:ABC-DEF...`).
2. Abre el chat con tu bot nuevo y envíale cualquier mensaje (ej. "hola").
3. En el navegador entra a `https://api.telegram.org/bot<TOKEN>/getUpdates` (reemplaza `<TOKEN>`).
   Busca `"chat":{"id":123456789` → ese número es tu **chat id**.

### 2. Subir el repositorio
1. Crea un repositorio nuevo en GitHub y sube todos estos archivos (incluida la carpeta oculta `.github`).
2. En el repo: **Settings → Secrets and variables → Actions → New repository secret** y crea:
   - `TELEGRAM_TOKEN` → el token del bot
   - `TELEGRAM_CHAT_ID` → tu chat id

### 3. Probar
**Actions → Vigilar tiendas → Run workflow**. La primera ejecución solo guarda los productos actuales; desde la segunda te avisa de los nuevos.

## Agregar o quitar tiendas
Edita `urls.txt` (se puede hacer desde la web de GitHub). Una URL por línea; si hace falta, un selector CSS después de ` | `.

## Notas
- **Repo público:** minutos de Actions ilimitados. Los secretos (token de Telegram) siguen siendo privados; solo se ven las URLs.
- **Repo privado:** 2.000 minutos gratis al mes. Cada 15 min son ~2.900 ejecuciones, así que cambia el cron a `*/30 * * * *` (~1.450 al mes).
- Para cambiar la frecuencia edita la línea `cron` en `.github/workflows/vigilar.yml` (mínimo 5 minutos).
- Si el repo pasa 60 días sin actividad, GitHub pausa las tareas programadas; se reactivan desde la pestaña Actions.
- Para empezar de cero, borra los archivos de la carpeta `estado/`.
