# Norus Intelligence Platform

Dashboard web hotelero comercial y operativo. Incluye backend API (FastAPI), interfaz HTML/CSS/JavaScript y soporte para SQLite local y PostgreSQL en la nube. La API y el frontend se sirven desde el mismo origen.

**Estado:** endurecimiento de seguridad completo (MFA, SSO opcional, cifrado de PII en reposo, CSRF y permisos por propiedad). Antes de usarlo con datos productivos valida las exportaciones reales del PMS, ejecuta las pruebas en staging y define respaldos externos.

## Funcionalidades

- **Comercial y revenue:** ocupación, ADR, RevPAR, ingresos, noches vendidas, reservas, anticipación, cancelaciones, canal de venta, segmentos y nacionalidades.
- **Métricas por período:** selector de granularidad **día / semana / mes / año**, rango `desde`/`hasta` y filtro por propiedad. Día y semana se agregan desde `hecho_metrica_diaria`; mes y año desde `hecho_metrica_mensual`.
- **Operación diaria:** tareas, incidencias, mantenimiento, inventario, objetos perdidos y checklists, con alcance por propiedad.
- **Habitaciones y dormitorios:** ocupación, ADR y RevPAR **por tipo** (con capacidad declarada) y **por número de habitación**, con noches vendidas, ingresos, ocupación del período e incidencias abiertas. Cada incidencia enlaza con el contexto del huésped de esa habitación.
- **Mercado hotelero:** comparación en tiempo real con **actualización diaria** contra referencias públicas verificables de Chile (INE-EMAT) con **fuente, URL, período y moneda** (ADR, RevPAR, ocupación). Se registran a mano, se importan en CSV/TSV/XLSX/JSON o se sincronizan desde fuentes públicas sin clave; la plataforma no estima precios de mercado y degrada al último válido si fallan las fuentes.
- **Gestión de datos:** CRUD de reservas, importación autenticada (CSV/TSV/XLSX/JSON), historial de cargas, auditoría y exportación CSV.
- **Seguridad:**
  - El dashboard `index.html` **no se entrega sin sesión**: el servidor responde `302` a `/login.html` y el frontend oculta la interfaz hasta confirmar `/api/auth/me`.
  - Sesión JWT en cookie `HttpOnly` + `SameSite=Lax`; CSRF de doble envío (`norus_csrf` + `X-CSRF-Token`) en escrituras.
  - Contraseñas con bcrypt, política de complejidad, bloqueo temporal de intentos y auditoría de accesos.
  - **MFA TOTP** (RFC 6238) con códigos de respaldo de un solo uso.
  - **SSO OIDC** opcional (Authorization Code + PKCE), sin alta automática de cuentas.
  - **Cifrado de datos en reposo** para PII (huésped y códigos de respaldo) con Fernet y rotación de claves.
  - **Permisos por propiedad** (`usuario_propiedad`): un usuario sin propiedades asignadas no accede a datos; administradores y cuentas con acceso global ven todo.
  - **Roles:** los tres roles ven el dashboard completo. `lector` solo consulta (PII oculta y sin escrituras), `editor` consulta y edita reservas/operación/importación, y `admin` además gestiona usuarios, propiedades, demo y eliminación de reservas. La auditoría es consultable por los tres roles.
  - Cabeceras CSP, HSTS en producción, control de origen, CORS cerrado y `/metrics` protegido.
- **Despliegue:** Docker Compose, migraciones Alembic, contenedores sin root, Caddy con HTTPS automático y respaldo diario de PostgreSQL.

Los KPI sin fuente suficiente se muestran como “—”; no se inventan datos.

## Tecnologías

| Capa | Tecnología |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic |
| Datos | SQLAlchemy 2.x, Alembic, SQLite (desarrollo) / PostgreSQL 18 (producción) |
| Interfaz | HTML5, CSS3, JavaScript moderno, Chart.js |
| Autenticación | JWT en cookie HttpOnly, bcrypt, TOTP, OIDC |
| Criptografía | `cryptography` (Fernet), sin dependencias externas para TOTP |
| Infraestructura | Docker Compose, Caddy, TLS automático |

## Inicio local (Windows / PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
alembic upgrade head
python -m scripts.create_admin

$env:NORUS_ENV = 'development'
$env:NORUS_COOKIE_SECURE = 'false'
$env:NORUS_JWT_SECRET = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(48))
$env:NORUS_ENCRYPTION_KEY = (python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Abre `http://127.0.0.1:8765`; serás redirigido a `login.html`. `create_admin` pide usuario y contraseña interactivamente (mínimo 12 caracteres y 3 tipos). Por defecto la base local es `data/norusluum.db`.

> **Clave de cifrado:** si pierdes `NORUS_ENCRYPTION_KEY`, los datos personales cifrados no se pueden recuperar. Guárdala en un gestor de secretos. Para rotar, define varias claves separadas por comas: la primera cifra y todas descifran.

## Pruebas

```powershell
pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests -q
```

La suite cubre login, CSRF, redirección del dashboard, granularidad día/semana/mes/año, CRUD, importación, MFA, SSO deshabilitado y permisos por propiedad.

## Despliegue con Docker Compose (PostgreSQL local)

1. Copia `.env.example` a `.env` y define:
   - `NORUS_JWT_SECRET` y `NORUS_ENCRYPTION_KEY` (secretos distintos).
   - `POSTGRES_PASSWORD`, `DOMAIN`, `ACME_EMAIL` y `NORUS_PUBLIC_URL`.
   - Opcionalmente `NORUS_SSO_ISSUER`, `NORUS_SSO_CLIENT_ID` y `NORUS_SSO_CLIENT_SECRET`.
2. Genera secretos:
   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(64))"
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
3. Levanta los servicios:
   ```powershell
   docker compose up -d --build
   docker compose exec api python -m scripts.create_admin
   docker compose ps
   ```
4. Abre `https://<tu-dominio>`.

Compose ejecuta `alembic upgrade head` antes de iniciar la API, no publica PostgreSQL al host y mantiene el servicio `backup` con `pg_dump` diario en el volumen `backup_data`.

## Migrar la base a PostgreSQL en la nube

Compatible con Neon, Supabase, Amazon RDS, Render, Railway y cualquier PostgreSQL con SSL.

1. Crea la base en el proveedor (Neon, Supabase, RDS, Render, Railway…) y copia su cadena **poolada**. Si la cadena viene como `postgres://...` o `postgresql://...`, cámbiala a `postgresql+psycopg://...`.
2. En `.env` define:
   ```dotenv
   DATABASE_URL=postgresql+psycopg://usuario:clave@host:5432/norus?sslmode=require
   NORUS_DB_SSLMODE=require
   NORUS_ENCRYPTION_KEY=<la misma clave usada en local>
   ```
3. Aplica el esquema y copia los datos:
   ```powershell
   .\.venv\Scripts\python.exe -m scripts.migrate_to_cloud --source sqlite:///./data/norusluum.db --yes
   ```
   El script aplica Alembic en el destino, exige que esté vacío (o `--replace`/`--append`), copia las tablas en orden de dependencias, reajusta las secuencias y verifica los recuentos.
4. Verifica:
   ```powershell
   .\.venv\Scripts\python.exe -c "from app.database import engine; from sqlalchemy import text; print(engine.connect().execute(text('select count(*) from usuario')).scalar())"
   ```
5. Para desplegar contra la nube usa `compose.cloud.yaml` (no levanta PostgreSQL local):
   ```powershell
   docker compose -f compose.cloud.yaml up -d --build
   ```

**Respaldo en la nube:** programa `pg_dump` con el proveedor o un cron externo, cifra el archivo y pruébalo restaurando en una base aislada. El respaldo del volumen local no cubre una base gestionada externa.

## Formato de importación

Se aceptan CSV, TSV, TXT, XLSX y JSON (array, envelopes `data`/`rows`/`registros` y NDJSON), con BOM UTF-8, UTF-16 o Windows-1252, hasta **1 GB, 1.000.000 de filas y 200 columnas**. Los archivos mayores de 4 MB se suben automáticamente en fragmentos con verificación SHA-256, por el límite de cuerpo de Vercel. Los límites vigentes se consultan en `GET /api/importar/config` y se configuran con `NORUS_IMPORT_MAX_ROWS`, `NORUS_UPLOAD_MAX_BYTES`, `NORUS_UPLOAD_CHUNK_BYTES` y `NORUS_ETL_BATCH_SIZE`. Campos reconocidos: fecha o mes/año, propiedad, moneda, ocupación, ADR, RevPAR, ingresos, noches, capacidad disponible, canal, nacionalidad, segmento, anticipación, código/huésped/estado de reserva, incidencias, área, satisfacción y NPS. Las referencias de mercado admiten además `fuente`, `metrica` (`adr`, `precio`, `revpar`, `ocupacion`), `periodo`, `valor`, `url`, `tipo_habitacion` y `notas`.

### Comparación con fuentes públicas reales (sin clave)

La fuente por defecto es **INE · EMAT (Chile)**, el libro oficial de la Encuesta Mensual de Alojamiento Turístico del Instituto Nacional de Estadísticas:

- URL: `https://www.ine.gob.cl/docs/default-source/actividad-del-turismo/cuadros-estadisticos-dos/serie-histórica-metodología-2017/series-mensuales-de-julio-2016-a-la-fecha.xlsx?sfvrsn=a6e68f7_97`.
- Cuadros 16 (ocupación, %), 22 (RevPAR, CLP) y 25 (ADR, CLP), por región y mes. Cifras oficiales, sin estimaciones; las provisionales se identifican con `/R` en el cuadro.
- Otros orígenes opcionales, **de otras geografías**, deben habilitarse explícitamente con `NORUS_MARKET_PUBLIC_SOURCES`: **EA Hospitality Pulse** (África Oriental, USD) y **Apartments Zürich** (Suiza, CHF).
- **Open Exchange Rate API** (`https://open.er-api.com/v6/latest/{MONEDA}`) solo convierte monedas; es auxiliar, no fuente turística.

La sincronización manual (`admin`/`editor`) y el **refresco diario** usan lista blanca de hosts HTTPS, timeout, límite de respuesta y caché. Cada referencia guarda fuente, URL, período, moneda y notas de atribución. `GET /api/mercado/tiempo-real` compara el warehouse interno con esas referencias en la misma moneda y unidad (ADR/RevPAR por habitación-noche, ocupación en %), con caché de 60 s. Si fallan las fuentes, degrada al último análisis válido y lo marca como `historico` con la señal `ultimo_valido`; **nunca inventa precios**.

#### Actualización diaria automática

`vercel.json` registra el cron `0 3 * * *` sobre `GET /api/cron/mercado-diario`, protegido con `Authorization: Bearer $CRON_SECRET`. El cron fuerza la lectura de fuentes, guarda las referencias y calienta la instantánea global en las monedas de `NORUS_CRON_MONEDAS` (por defecto `CLP,USD`). Las vistas por propiedad se calculan bajo demanda con esas referencias ya frescas; para calentarlas todas en el cron, define `NORUS_CRON_WARM_PROPERTIES=1`.

El navegador aplica *stale-while-revalidate* en `sessionStorage`: pinta el último snapshot válido antes de esperar al servidor y lo reemplaza al llegar la respuesta. Si el snapshot tiene más de 24 h, fuerza `refresh=1` como respaldo aunque el cron no esté disponible. Para forzar una lectura inmediata, usa el botón de actualización del apartado Mercado.

- Los archivos grandes se procesan en memoria; el navegador y la Function necesitan RAM suficiente (especialmente XLSX, que se expande antes de leerse). La subida fragmentada resuelve el límite de cuerpo de Vercel; para cargas muy por encima de 1 GB usa Docker/Caddy.
- Usa la moneda ISO 4217 de cada fila; si falta, se aplica `NORUS_DEFAULT_CURRENCY`. Los importes de monedas distintas no se suman.
- “Habitaciones disponibles” debe representar capacidad del período en noches-habitación, no solo habitaciones físicas.
- Los hechos mensuales se reemplazan por propiedad/mes/moneda al reimportar. Importa extracciones completas del período.
- Si el archivo solo contiene reservas, ingresos y noches son valores de libro de reservas, no ingresos contables conciliados.
- La carga de archivos no es una integración directa con el PMS/OTA.

## Datos de demostración

La demo es **real y verificable**. `scripts/seed_demo_real.py` (y `POST /api/demo/cargar`) descarga el dataset hotelero de Antonio, de Almeida & Nunes (2019), *Hotel booking demand datasets*, Data in Brief 22, 41-49, DOI `10.1016/j.dib.2018.11.126`, licencia **CC BY 4.0** (119.390 reservas). Carga hasta 120.000 filas; ADR original en EUR se convierte a CLP con el tipo de cambio real de `open.er-api.com` leído en el momento, y se registran además las referencias reales de INE-EMAT con su URL y período. La capacidad de 100 habitaciones por hotel (20 por tipo) es una **referencia declarada**, no un dato observado, y queda documentada en `CargaArchivo.mensaje_error`.

```powershell
.\.venv\Scripts\python.exe -m scripts.seed_demo_real --filas 25000
```

- `--filas` fija el número de reservas (por defecto 25.000).
- El borrado (`DELETE /api/demo` o `scripts.seed_demo.limpiar_demo`) es **selectivo**: elimina solo la demo marcada como `seed_demo`, respeta importaciones reales posteriores y nunca toca usuarios ni auditoría.
- `scripts/seed_demo.py` sigue disponible para generar archivos de muestra sintéticos (`--out`) para probar la importación; esos valores sí se describen como ilustrativos y llevan `fuente="demo (generado)"`.

## Despliegue en Vercel

El proyecto incluye el adaptador `api/index.py`, `vercel.json` (función Python 3.12, 300 s, 1 GB, cron diario) y `pyproject.toml` con `tool.vercel.entrypoint = "api.index:app"`. `.vercelignore` y `excludeFiles` evitan subir los archivos pesados de demo y la base SQLite local. Variables nuevas: `CRON_SECRET`, `NORUS_MARKET_PUBLIC_SOURCES=ine_emat`, `NORUS_CRON_MONEDAS=CLP,USD` y, opcionalmente, `NORUS_CRON_WARM_PROPERTIES`.

> **Importante:** Vercel limita el cuerpo de petición/respuesta de una Function a **4.5 MB** (`FUNCTION_PAYLOAD_TOO_LARGE`). El dashboard resuelve esto con subida fragmentada de 4 MB ensamblada en PostgreSQL, con verificación SHA-256, hasta 1 GB / 1.000.000 de filas. SQLite en `/tmp` es efímero: en producción **es obligatorio `DATABASE_URL`** con PostgreSQL gestionado y la migración `f4a7c9e1b253` aplicada.

### Paso a paso con Vercel CLI

1. Instala la CLI y autentica:
   ```powershell
   npm i -g vercel@latest
   vercel login
   cd "C:\Users\pardo\Desktop\Norus_Intelligence_Platformv1"
   vercel link
   ```
2. Genera secretos distintos (guárdalos en un gestor de secretos):
   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(64))"
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
3. Añade las variables para `production` (el comando te pedirá el valor):
   ```powershell
   vercel env add NORUS_ENV production
   vercel env add NORUS_JWT_SECRET production
   vercel env add NORUS_ENCRYPTION_KEY production
   vercel env add NORUS_COOKIE_SECURE production
   vercel env add NORUS_PUBLIC_URL production
   vercel env add DATABASE_URL production
   ```
   Valores: `NORUS_ENV=production`, `NORUS_COOKIE_SECURE=true`, `NORUS_PUBLIC_URL=https://<tu-dominio>.vercel.app` y la cadena poolada de PostgreSQL en `DATABASE_URL`. Deja `NORUS_CORS_ORIGINS` vacío. Si usas SSO, añade también `NORUS_SSO_ISSUER`, `NORUS_SSO_CLIENT_ID` y `NORUS_SSO_CLIENT_SECRET`.
4. Despliega y verifica:
   ```powershell
   vercel --prod
   ```
   Abre `https://<deployment>/api/health`; debe devolver `{"status":"ok",...}`. Después entra a `/login.html`.

### Paso a paso desde Git

1. Inicializa el repositorio si aún no existe:
   ```powershell
   git init
   git add .
   git commit -m "Norus listo para Vercel"
   git remote add origin https://github.com/<usuario>/<repo>.git
   git push -u origin main
   ```
2. En Vercel → **Add New → Project**, importa el repositorio. Framework Preset: **FastAPI**; Root Directory: `.` ; Build Command y Output Directory: vacíos.
3. Configura las mismas variables de entorno del paso 3 anterior para **Production**.
4. Pulsa **Deploy** y comprueba `/api/health`.

> Si migras una base local con datos cifrados, usa exactamente la misma `NORUS_ENCRYPTION_KEY` en local, en el script de migración y en Vercel. Sin esa clave no se pueden descifrar los datos personales.

## Endpoints principales

- `POST /api/auth/login`, `POST /api/auth/mfa/verify`, `POST /api/auth/logout`, `GET /api/auth/me`
- `POST /api/auth/password`, `POST /api/auth/mfa/setup|enable|disable`
- `GET /api/auth/sso/status`, `GET /api/auth/sso/login`, `GET /api/auth/sso/callback`
- `GET /api/dashboard?moneda=CLP&granularidad=dia|semana|mes|anio&desde=YYYY-MM-DD&hasta=YYYY-MM-DD&propiedad_id=N`
- `GET /api/dashboard/monedas`, `GET /api/dashboard/propiedades`
- `GET/POST/PUT/DELETE /api/reservas`
- `POST /api/importar`, `GET /api/cargas`
- `GET/POST/PATCH /api/operaciones/{tareas|incidencias|mantenimiento|inventario|objetos-perdidos|checklists}`
- `GET/POST/PATCH /api/usuarios` y `POST /api/usuarios/{id}/reset-password` (solo administradores)
- `GET /api/auditoria` (todos los roles, solo lectura), `GET /api/health`
- OpenAPI en `/api/docs` solo en desarrollo; deshabilitado en producción.

## Antes de usar datos productivos

- Acordar con la hotelera el significado, zona horaria, granularidad, estados, moneda, impuestos y datos personales permitidos en cada exportación.
- Implementar y probar el conector oficial del PMS/OTA (credenciales, sincronización incremental, cancelaciones y conciliación).
- Validar mapeos, integridad y cálculos con muestras autorizadas de cada propiedad; acordar criterios de aceptación con operaciones, revenue y finanzas.
- Ejecutar pruebas funcionales, de carga, restauración, accesibilidad y penetración en staging.
- Definir monitoreo/alertas, retención de auditoría, respaldos cifrados externos y procedimiento de incidentes.
- Configurar MFA obligatorio para administradores y revisar periódicamente los permisos por propiedad.

## Estructura

- `app/`: API, autenticación, criptografía, SSO, ETL, agregados y operaciones hoteleras.
- `alembic/`: migraciones versionadas.
- `js/`, `css/`, `index.html`, `login.html`: interfaz web.
- `vendor/`: dependencias JavaScript empaquetadas localmente.
- `scripts/create_admin.py`: alta inicial de administrador.
- `scripts/seed_roles.py`: alta idempotente de `editor` y `lector` con propiedades Norus Luum asignadas (contraseñas desde variables de entorno).
- `scripts/seed_demo.py`: datos de demostración (>100.000 filas) y archivos de muestra multi-formato.
- `scripts/seed_ops_demo.py`: operativa de demostración (incidencias con satisfacción, tareas, mantenimiento, inventario, objetos perdidos y checklists) derivada de la demo existente, sin modificar datos previos.
- `scripts/migrate_to_cloud.py`: copia y verificación de datos hacia PostgreSQL en la nube.
- `api/index.py`, `vercel.json`: adaptador serverless para Vercel.
- `compose.yaml`, `compose.cloud.yaml`, `Dockerfile`, `Caddyfile`: despliegue local y en la nube.
- `.env.example`: configuración sin secretos.
