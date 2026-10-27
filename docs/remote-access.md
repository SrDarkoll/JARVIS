# Acceso remoto privado (móvil / PWA)

Esta guía describe el único modo soportado: JARVIS escucha en loopback y un proxy
HTTPS privado de la VPN del usuario lo publica al tailnet. **No abras el puerto
a Internet ni uses Tailscale Funnel.**

> **Estado:** backend de pairing/sincronización y PWA móvil implementados. El
> shell incluye chat, historial, reconexión, revocación y voz Live; falta
> validarlo en un teléfono físico sobre Tailscale Serve.

## Requisitos

- JARVIS en `http://localhost:5002` (el valor por defecto).
- Tailscale instalado y conectado en el PC y en el móvil, con MagicDNS y HTTPS
  habilitados en el tailnet.
- Un nombre de máquina genérico antes de habilitar certificados: el nombre del
  host aparece en Certificate Transparency.
- `JARVIS_API_TOKEN` configurado. Sin token, el modo remoto se rechaza.

## Configuración

En `.env`:

```dotenv
JARVIS_HOST="localhost"
JARVIS_PORT="5002"
JARVIS_REMOTE_BASE_URL="https://<host-generico>.<tailnet>.ts.net"
JARVIS_API_TOKEN="<token-largo-y-aleatorio>"
JARVIS_CORS_ORIGINS="http://127.0.0.1:5002,http://localhost:5002"
```

`JARVIS_REMOTE_BASE_URL` debe ser exactamente el origen HTTPS, sin path, query,
fragmento, usuario ni contraseña. JARVIS no instala Tailscale, no configura Serve,
no abre puertos y no modifica el firewall.

Publica el listener local con Tailscale Serve:

```powershell
tailscale serve --bg 5002
tailscale serve status
```

No uses `tailscale funnel`.

## Emparejamiento

1. Abre el HUD de escritorio desde `http://localhost:5002`.
2. Pulsa **Vincular móvil** en el panel de comandos. El diálogo muestra el QR,
   el PIN, la caducidad y los dispositivos ya vinculados.
3. Genera el QR desde el panel de dispositivos. El PIN va en el fragmento
   `#pair=...`, nunca en la query string.
4. Abre el QR en el móvil dentro del tailnet y confirma el emparejamiento.
5. El móvil recibe un token de dispositivo de un solo uso. El token maestro
   `JARVIS_API_TOKEN` nunca se entrega al móvil.
6. Revoca el dispositivo desde el HUD o desde el propio móvil cuando ya no lo
   necesites.

El backend emite tickets WebSocket de corta duración y atados a una ruta; el
token de dispositivo no se coloca en la URL.

## Comportamiento de seguridad

- El listener sigue en loopback. El proxy llega a `127.0.0.1`, así que JARVIS
  detecta el tráfico remoto por el origen HTTPS configurado, por el `Host` del
  proxy o por `Tailscale-User-Login`; nunca por confiar en loopback a secas.
- Si `JARVIS_REMOTE_BASE_URL` está configurado, las rutas protegidas exigen token
  maestro, cookie local o token de dispositivo. Una petición sin origen local
  explícito no hereda permisos de owner.
- El dispositivo queda atado al perfil que inició el emparejamiento. No se acepta
  un `profile_id` enviado por el cliente.
- Los comandos remotos pasan por el mismo pipeline, política, confirmación e
  idempotencia que los locales.
- El historial de sync es por perfil y con ventana acotada; si el cursor queda
  fuera de la ventana o el backend se reinicia, el cliente debe pedir un snapshot.
- No se cachean respuestas autenticadas ni audio en el service worker.

## Diagnóstico

- `GET /api/health` debe responder 200 en local.
- `tailscale serve status` debe mostrar el origen HTTPS.
- Si el móvil no carga, comprueba que el PC y el móvil están en el mismo tailnet
  y que el nombre del certificado coincide exactamente con
  `JARVIS_REMOTE_BASE_URL`.
- Si el WebSocket queda en “reconectando”, comprueba primero el certificado:
  `tailscale cert <host>.<tailnet>.ts.net`, luego
  `tailscale serve reset` y `tailscale serve --bg 5002`. El certificado de
  Tailscale caduca; un certificado expirado rompe WSS y la instalación PWA.
- Cierra y vuelve a abrir la PWA después de renovar el certificado. Si persiste,
  revoca el dispositivo y genera un PIN nuevo.
- `ConnectionResetError [WinError 10054]` en el log suele ser el cierre normal de
  una conexión del móvil, no la causa del fallo.
- Si el pairing devuelve `remote_access_not_configured`, revisa
  `JARVIS_REMOTE_BASE_URL` y reinicia el backend.
- Si una ruta devuelve `Invalid or missing token`, configura `JARVIS_API_TOKEN`
  o empareja el dispositivo de nuevo.

## Fuera de alcance

WebRTC, relés públicos, Funnel, port forwarding, ejecución offline de comandos y
mezcla implícita de perfiles no forman parte de esta versión.
