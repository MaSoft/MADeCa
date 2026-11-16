# MADeCa

**Software libre y gratuito desarrollado por [Masoft.es](https://masoft.es)** · Licencia [MIT](LICENSE)

Plataforma SaaS multi-tenant para la verificación y firma opcional del
**Documento Electrónico de Control Administrativo (DeCA)** exigido en el
transporte público de mercancías por carretera en España, con
integración agnóstica de ERPs (a3ERP, Sigrid, SAP, Dynamics 365, Odoo,
Sage o cualquier sistema propio).

## Dos caminos de ingesta, a elección del ERP de origen

MADeCa admite **dos formas distintas** de dar de alta una expedición,
porque no todos los ERP están en la misma situación:

**1. Passthrough** — el ERP ya genera su propio PDF (albarán, factura o
declaración de contenido). MADeCa no lo redacta, solo:

1. Comprueba que no tiene pinta de ser un escaneo (heurística: página
   sin texto extraíble con solo una imagen incrustada) — la norma exige
   PDF nativo digital, no papel escaneado (Apartado Segundo.2,
   BOE-A-2026-12784).
2. Estampa el código QR de verificación en la primera página, **sin
   tocar el resto del contenido**.
3. Lo firma con AdES si el cliente lo marca como de finalidad
   contractual, y lo sirve en `/inspect/{id}`.

Esto es legal porque el DeCA no es un impreso oficial específico: puede
ser cualquier documento del operador siempre que contenga los datos del
Art. 6 de la Orden FOM/2861/2012. MADeCa no valida ese contenido — no
hace parsing ni OCR (sería frágil y nada fiable para un sistema legal);
es responsabilidad de la plantilla del ERP del cliente.

**2. Generación** — el ERP no produce un PDF propio, solo manda los
datos estructurados (cargador, transportista, mercancía...). Aquí
MADeCa sí redacta el documento y sí valida los campos mínimos del Art. 6
antes de generarlo.

Cuál se usa lo decide el propio adaptador según si llega o no un fichero
PDF junto a los datos — ver la sección de integración ERP más abajo.

## Base legal

- **BOE-A-2026-12784** — Resolución de 5 de junio de 2026, DGTT y
  Ferrocarril (BOE de 12/06/2026). Fija las características técnicas
  del DeCA; no tiene cláusula propia de entrada en vigor.
- **Ley 9/2025, de Movilidad Sostenible** (BOE-A-2025-24545, en vigor
  el 05/12/2025), disposición transitoria octava: el documento de
  control es **obligatoriamente digital desde el 5 de octubre de 2026**
  (diez meses desde su entrada en vigor).
- **Orden FOM/2861/2012**, art. 6 — datos mínimos del documento de
  control (transporte de mercancías). Su art. 7 lo modificó la Orden
  TRM/282/2026 (responsable de cada dato: cargador a-d, transportista
  e-g).
- **Reglamento (UE) 910/2014 (eIDAS)** — solo aplica si el DeCA tiene
  finalidad contractual (firma AdES).

Puntos que conviene tener presentes porque contradicen suposiciones
habituales sobre esta norma:

| Idea habitual | Lo que dice realmente el BOE |
|---|---|
| "Hay que firmar el PDF digitalmente" | La firma **no es obligatoria**; solo si el documento tiene finalidad contractual, y basta con AdES simple (no PAdES-LTV, no sello de tiempo) |
| "Hay que guardar el documento para siempre" | Conservación mínima **1 año** (Apartado Segundo.4) |
| "La URL del QR debe estar siempre activa" | Puede desactivarse a partir de **7 días** tras finalizar el servicio (Apartado Tercero.5) |
| "Hace falta HSM / KMS" | No lo exige la norma; es una decisión de infraestructura, no un requisito legal |

Este código deliberadamente **no** implementa HSM físico, Kafka, Merkle
trees, biometría en la entrega, sello de tiempo TSA ni Edge Computing —
ninguno de estos aparece en la resolución.

## Arquitectura

Stack: **Python 3.12 + FastAPI + PostgreSQL + Jinja2/HTMX**. Un único
lenguaje. El estampado de QR usa `pypdf` + `reportlab` (puro Python, sin
librerías nativas) — funciona igual en Windows que en Docker.

```
app/
  domain/       Esquema de metadatos (Pydantic), validación NIF/CIF/matrícula, modelos ORM
  adapters/     Ingesta ERP: hot-folder (PDF + sidecar) + mapeo de campos
  services/     Estampado de QR, firma AdES, almacenamiento, retención, expediciones
  security/     Auth (tenant/superadmin/API keys), rate limiting, cifrado de secretos
  routers/      Visor público, ingesta REST, dashboard de tenant, panel de superadmin
  templates/    Jinja2 (dashboard HTMX)
migrations/     Alembic
tests/          pytest (dominio, mapeo ERP, estampado de QR, retención — sin red)
scripts/        Alta de superadmin/tenant, purga opcional de retención
```

### Multi-tenancy y dos niveles de acceso

Cada tabla operativa lleva `tenant_id`. Todas las consultas del
dashboard y de la API de ingesta filtran explícitamente por el tenant de
la sesión/API key — nunca por un parámetro de la petición.

- **`/superadmin`** — panel del operador de la plataforma (tú). Da de
  alta empresas nuevas, las activa/desactiva, y puede ver/resetear
  usuarios de cualquier empresa. Autenticación totalmente separada del
  dashboard de tenant (cookie distinta, tabla `superadmins` sin relación
  con `tenants`/`usuarios`, claim JWT `"tipo"` verificado en cada
  petición).
- **`/dashboard`** — panel de cada empresa cliente. Roles `admin`
  (gestiona usuarios/integraciones/certificados), `operador` (gestiona
  expediciones) y `lectura` (solo consulta).
- **`/inspect/{id}`** — el único recurso verdaderamente público, sin
  login. El propio UUIDv4 actúa como control de acceso (no enumerable).

### Los dos caminos de integración ERP

1. **Hot-folder** (`app/adapters/hotfolder_watcher.py`): para a3ERP,
   Sigrid u otro ERP sin API. Se separa en dos subcarpetas, una por cada
   modo, para que no haya ninguna ambigüedad sobre qué camino aplica a
   cada fichero:

   ```
   hotfolder_watch/incoming/<tenant_id>/<erp_origen>/pdf/
       ALB-2026-001.pdf     <- generado por el ERP, tal cual
       ALB-2026-001.json    <- sidecar: {"referencia_externa": "ALB-2026-001",
                                 "matricula_vehiculo": "1234BBC",
                                 "fecha_transporte": "2026-08-10T09:00:00Z"}

   hotfolder_watch/incoming/<tenant_id>/<erp_origen>/datos/
       pedido123.json        <- datos completos (cargador, transportista,
                                 mercancía...); puede ser una lista para
                                 reparto multipunto (varias filas = varios
                                 destinos desde el mismo origen)
   ```

   En `pdf/`, el PDF y el sidecar pueden llegar en cualquier orden; se
   procesan cuando ambos están presentes y estables. Usa
   `PollingObserver` (no el `Observer` nativo de `watchdog`): los bind
   mounts de Docker Desktop en Windows/Mac no propagan eventos inotify de
   forma fiable cuando el fichero se escribe desde el lado host — con el
   observer nativo el watcher se queda "sordo" y nunca procesa nada, sin
   ningún error visible. También hace un barrido de lo que ya hubiera en
   la carpeta al arrancar, para no dejar huérfanos tras un reinicio del
   contenedor.

2. **REST**, dos endpoints, uno por camino:
   - `POST /api/v1/expediciones` (`multipart/form-data`, cabecera
     `X-API-Key`): el PDF se sube como fichero adjunto.
   - `POST /api/v1/expediciones/datos` (JSON, cabecera `X-API-Key`): sin
     fichero, datos completos.

3. **Mapeo configurable** (`app/adapters/mapping.py`): traduce los
   nombres de campo del ERP origen a nuestros esquemas — mapeos
   independientes para cada camino (`MAPEOS_METADATA_*` /
   `MAPEOS_DATOS_*`). **Importante**: los mapeos de a3ERP y Sigrid
   incluidos son plantillas orientativas, no verificadas contra una
   instalación real — ni a3ERP ni Sigrid publican un esquema de
   exportación estable. Antes de dar de alta un cliente con uno de estos
   ERPs, confirma los nombres de campo reales de su export/sidecar y
   ajusta el mapeo correspondiente.

## Ciclo de vida del DeCA

- **Modificación durante el servicio** (Apartado Quinto, método 1): se
  añade al mismo PDF una página con el motivo y los datos anteriores
  marcados como no válidos; la URL y el QR no cambian y se conserva la
  versión anterior. Panel (detalle de la expedición) o
  `POST /api/v1/expediciones/{id}/modificaciones`.
- **Copia para el conductor** (Apartado Séptimo): PDF vigente y QR en
  PNG, en el panel o con `GET /api/v1/expediciones/{id}/pdf` y `/qr.png`.
- **Otra parte** (Apartado Segundo.4): enlace privado `/descarga/{token}`
  válido durante el plazo de conservación (`url_contraparte` en la API).
- **Cabotaje**: matrículas extranjeras con `pais_matricula_vehiculo` /
  `pais_matricula_remolque` (ISO 3166-1, `ES` por defecto).

## Obtención de datos: bandeja de revisión

Todo lo que se obtiene de documentos (albaranes, cartas de porte, CMR;
PDF, escaneo o foto) o de un ERP llega como **borrador** a la bandeja.
Una persona lo revisa junto al original y lo confirma; solo entonces se
emite el DeCA (un escaneo nunca se emite como DeCA, Apartado Segundo.2;
el cargador y el transportista responden de los datos, Art. 7 FOM).

Tres vías:

1. **Panel** → Bandeja de revisión → subir documentos.
2. **Fuentes de datos** (servidor, servicio `conectores`): SharePoint /
   OneDrive (Graph), Google Drive, Dropbox, SFTP, carpeta del servidor
   (solo instalaciones propias) y ERP: Business Central, Odoo (JSON-2),
   SAP Business One y Holded (estos dos con correspondencia de campos
   editable y por verificar). Credenciales cifradas; protección SSRF.
3. **Conector local** (`conector_local/`): se instala en la red del
   cliente, extrae allí y solo envía datos (`POST /api/v1/borradores`).

OCR: Tesseract en el servidor por defecto. Azure AI Document
Intelligence solo para las empresas que lo autoricen expresamente
(subencargado, art. 28.2 RGPD; queda quién y cuándo). El documento
original se borra al emitir o descartar el borrador.

## Puesta en marcha (Docker)

```bash
cp .env.example .env
# edita .env: APP_SECRET_KEY, APP_BASE_URL, JWT_SECRET_KEY con valores propios

docker compose up -d --build
docker compose exec app alembic upgrade head   # también tras cada actualización

# Alta del primer superadmin (operador de la plataforma)
docker compose exec app python -m scripts.crear_superadmin \
    --email operador@tudominio.es --password "una-clave-fuerte"
```

En producción (`APP_ENV=production`) MADeCa exige `APP_BASE_URL` con
`https://` (Apartado Tercero.1) y no arranca sin ella: ponlo detrás de un
proxy HTTPS con TLS 1.2 o superior, no publiques el puerto 8000, e indica
en `FORWARDED_ALLOW_IPS` la IP del proxy (ver `.env.example`). Si los DeCA
son de transportes en Canarias, `ZONA_HORARIA=Atlantic/Canary`.

- Panel de operador: http://localhost:8000/superadmin/login — da de alta
  empresas desde ahí (crea el tenant + su primer usuario admin).
- Dashboard de cada empresa: http://localhost:8000/login
- Visor público: http://localhost:8000/inspect/{id}
- Documentación interactiva (OpenAPI/Swagger): http://localhost:8000/docs

## Desarrollo local sin Docker

Todo el código es puro Python (sin dependencias nativas como GTK), así
que la suite de tests corre igual en Windows que en Linux:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
pytest tests/ -v
```

Para levantar el servidor completo localmente hace falta PostgreSQL y
Redis accesibles (`DATABASE_URL` / `REDIS_URL` en `.env`).

## Ingesta REST — ejemplos

Camino 1, PDF ya generado por el ERP:

```bash
curl -X POST "https://tu-dominio/api/v1/expediciones?erp_origen=generico" \
  -H "X-API-Key: mad_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  -F "referencia_externa=ALB-2026-001" \
  -F "matricula_vehiculo=1234BBC" \
  -F "fecha_transporte=2026-08-10T09:00:00Z" \
  -F "finalidad_contractual=false" \
  -F "archivo=@albaran.pdf"
```

Camino 2, sin PDF propio — MADeCa redacta el documento:

```bash
curl -X POST "https://tu-dominio/api/v1/expediciones/datos?erp_origen=generico" \
  -H "X-API-Key: mad_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "referencia_externa": "ALB-2026-002",
    "cargador": {"nombre_razon_social": "Transportes Ejemplo SL", "nif_cif": "B00345678", "domicilio": "Calle Industria 14, Madrid"},
    "transportista": {"nombre_razon_social": "Transportista Efectivo SL", "nif_cif": "B00345678"},
    "origen_direccion": "Calle Industria 14, Madrid",
    "destino_direccion": "Polígono Sur, Sevilla",
    "mercancia": {"naturaleza": "Palés de material eléctrico", "peso_kg": 850.5},
    "fecha_transporte": "2026-08-10T09:00:00Z",
    "matricula_vehiculo": "1234BBC"
  }'
```

Marcar como entregado (empieza la cuenta atrás de 7 días de la URL
pública, Apartado Tercero.5):

```bash
curl -X PATCH "https://tu-dominio/api/v1/expediciones/{id}/finalizar" \
  -H "X-API-Key: mad_live_..."
```

## Seguridad

- API keys: solo se guarda el hash (SHA-256) + prefijo; la clave en
  claro solo se muestra una vez al crearla.
- Contraseñas de usuario: `bcrypt` directo (no passlib — sin
  mantenimiento desde 2020, rompe con bcrypt>=4.1).
- Sesión del dashboard/superadmin: cookies JWT `httponly` **distintas**
  entre tenant y superadmin, con claim `"tipo"` verificado en cada
  petición; `secure` fuera de desarrollo; `samesite=lax` (mitiga CSRF en
  navegadores modernos sin necesitar un token CSRF adicional).
- Certificados `.p12`/`.pfx`: la contraseña se cifra en reposo (Fernet)
  con clave derivada de `APP_SECRET_KEY`. Para volumen alto de
  certificados, sustituir por AWS KMS/Azure Key Vault/HashiCorp Vault.
- Visor público: IDs UUIDv4 no enumerables, sin login y **sin límite de
  peticiones por IP** (Apartado Tercero.4: la seguridad no puede impedir
  la descarga directa; un límite por IP bloquearía a agentes detrás de la
  misma IP pública). La protección frente a denegación de servicio va en
  el proxy/WAF.
- Desactivar una empresa bloquea login y API keys de inmediato — se
  comprueba en el propio `POST /login`, no solo en peticiones
  posteriores.
- Cabeceras de seguridad (CSP, X-Frame-Options, HSTS, nosniff) y límite
  de tamaño de petición aplicados globalmente.

**Nota de despliegue importante**: detrás de un proxy inverso
(nginx/traefik/load balancer), el proxy debe enviar `X-Forwarded-For` y
`FORWARDED_ALLOW_IPS` debe contener solo la IP del proxy: uvicorn toma
entonces la IP real del usuario para el límite de intentos de acceso y el
registro de lecturas del QR. No uses `*` si el puerto de MADeCa es
accesible desde otros equipos: cualquiera podría falsear su IP. El
paquete de instalación lo configura con `--ip-proxy`.

## Limitaciones conocidas / fuera de alcance de esta versión

- **Camino 1 (passthrough): MADeCa no valida el contenido del PDF del
  ERP**, solo comprueba que no parezca un escaneo (heurística). Que el
  PDF contenga de verdad los datos del Art. 6 es responsabilidad de la
  plantilla del ERP del cliente. En el camino 2 (generación) sí se
  validan.
- **Adaptador SQL/ODBC directo**: no implementado — hot-folder + REST
  cubren los casos reales de a3ERP/Sigrid sin añadir una dependencia sin
  usar.
- **Firma con HSM/KMS**: la contraseña del certificado se cifra en la
  propia base de datos (Fernet). Migrar a un KMS gestionado cuando el
  volumen de certificados custodiados lo justifique.
- **Almacenamiento**: por defecto en un volumen Docker local. Hay un
  backend S3/MinIO ya implementado (`STORAGE_BACKEND=s3` en `.env`) para
  cuando se necesite almacenamiento fiable a largo plazo.
- **Purga automática por retención**: `scripts/purgar_expirados.py`
  existe pero es manual y en modo `dry-run` por defecto — borrar datos de
  un tenant es una decisión de negocio, no algo que deba automatizarse
  sin más.
- **eFTI / interoperabilidad europea**: la norma prevé el Reglamento (UE)
  2020/1056 como vía de exención alternativa, no como requisito a
  implementar en paralelo; no está mapeado en esta versión.

## Testing

```bash
pytest tests/ -v
```

120 tests (dominio, seguridad, firma, modificaciones, extracción,
bandeja, conectores con los servicios externos simulados, conector
local), todos sin infraestructura externa (ni base de datos, ni Redis, ni
Docker). Los 2 del PDF generado con WeasyPrint se omiten en Windows sin
GTK.

## Licencia

Publicado bajo licencia [MIT](LICENSE): puedes usarlo, modificarlo y redistribuirlo manteniendo el aviso de copyright. Se entrega «tal cual», sin garantía de ningún tipo. Cumplir la normativa del DeCA (BOE-A-2026-12784, Orden FOM/2861/2012) es responsabilidad de la empresa que lo usa.

© 2026 Masoft.es
