---
description: Cómo se conectan frontend y backend en ambiente de desarrollo local. No define exposición en producción — eso es decisión de Operaciones. Aplicar al configurar entornos de desarrollo local o docker-compose.
activation: model_decision
---

# Conectividad local — Frontend / Backend

> Este documento define **solo desarrollo local**. La exposición en producción (URL
> pública de Cloud Run, Ingress de GKE, API Gateway, etc.) es una decisión de
> Operaciones, no de este documento — ver nota al final.

## Modo A — Ejecución separada

Backend y frontend corren como procesos independientes en la máquina del
desarrollador (`uvicorn`/`fastapi dev` por un lado, `ng serve` por otro).

- El navegador llama **directo** a la URL del backend (ej. `http://localhost:8000`),
  configurada en `environment.ts` (build local, sin config de CLI).
- **Requisito no negociable**: el backend debe tener CORS habilitado explícitamente
  para el origen de desarrollo (`http://localhost:4200` o el puerto que use `ng serve`).
  Sin esto, el navegador bloquea la llamada aunque ambos servicios estén corriendo
  correctamente — no es un problema del código, es la política de origen del browser.
- Este modo es el más simple para iterar rápido en un solo servicio a la vez.

## Modo B — docker-compose

Backend y frontend corren en contenedores dentro de la misma red de docker-compose.

- El contenedor del frontend sirve el bundle compilado vía **Nginx** (nuestro estándar
  de despliegue, ver `frontend-angular.md`).
- **Patrón recomendado**: configurar Nginx como *reverse proxy* para las rutas de API,
  no exponer el backend directo al navegador. El frontend llama a una ruta relativa del
  mismo origen (`/api/...`) y es Nginx quien la reenvía internamente al backend por
  nombre de servicio de Docker (`http://backend:8000`, resuelto por DNS interno de
  docker-compose).
- **Ventaja sobre el Modo A**: como el navegador nunca ve el backend directamente, no
  hay CORS que configurar en este modo — el navegador solo ve un origen.
- Ejemplo de bloque a agregar en `default.conf` (Nginx del frontend):
  ```
  location /api/ {
      proxy_pass http://backend:8000/;
  }
  ```
  El nombre `backend` debe coincidir con el nombre del servicio definido en
  `docker-compose.yml`.

## Elegir entre Modo A y Modo B

- Modo A para iterar rápido sobre un solo servicio.
- Modo B cuando se necesita probar la integración completa como se comportará una vez
  contenerizada, o para evitar lidiar con CORS en desarrollo.
- Ningún modo es "el correcto" de forma universal — el agente puede usar el que el
  desarrollador tenga configurado en el proyecto; no debe migrar de uno a otro por su
  cuenta.

## Modo mock — datos falsos para sistemas externos sin instancia real en dev

Aplica cuando un `repository`/`service` del backend o del DAL toca un
sistema externo (otra base de datos, AS400, LDAP, cualquier API de
terceros) que **no tiene una instancia de desarrollo/QA accesible** —
situación real y común en migraciones de monolitos legados, donde el
entorno de desarrollo no tiene VPN/credenciales a los sistemas
productivos. Sin esto, ni el backend ni el frontend se pueden probar de
verdad (ni siquiera navegar pantallas) fuera de la red corporativa.

**Patrón:** cada función pública que conecta al sistema externo empieza
con un guard, ANTES de conectar, leyendo una variable de entorno booleana
(`Settings.mockup: bool = False`, env var `MOCKUP`, default apagado):

```python
def consultar_algo(id: str) -> list[AlgoRow]:
    if get_settings().mockup:
        return [AlgoRow(identificador=id, campo_x="valor de prueba", ...)]
    conn = _conectar_sistema_real()
    ...  # código real sin cambios
```

- La data mock devuelve la MISMA forma Pydantic que el camino real —nunca
  `dict` crudo—, para que el resto del código (router, service, frontend)
  no distinga si vino de mock o de la fuente real.
- **Si la función recibe un identificador de entrada** (folio, ID, RUT),
  la fixture le hace **eco** en el campo correspondiente del resultado —
  así buscar algo específico en el frontend y verlo reflejado sirve de
  prueba real de navegación, no solo de "la pantalla no rompe".
- **Si no recibe identificador** (ej. consulta por rango de fechas), una
  lista fija de 1-2 filas de ejemplo alcanza.
- El guard vive en CADA función pública afectada, no en un wrapper
  genérico ni en el router que la llama — mantiene el resto del código
  (manejo de errores, conexión real) intacto y visible.
- Un login/autenticación (ej. LDAP) en modo mock usa un **usuario de
  prueba fijo hardcodeado** (ej. `{"testuser": "testpass123"}`), NO acepta
  cualquier credencial no vacía — un login mal tipeado tiene que seguir
  fallando, mismo comportamiento que el sistema real, y evita que el modo
  mock se convierta en un bypass de auth demasiado laxo si queda prendido
  por error en algún ambiente.
- Un repositorio que SÍ tiene una instancia real accesible en cualquier
  entorno de desarrollo (ej. la base de datos propia del proyecto,
  containerizada vía `docker-compose`) **no se mockea** — mockearlo resta
  valor a la prueba sin resolver ninguna limitación real.

**Para el Planner:** al escribir `detalle_tecnico` de un item cuyo
`repository`/`service` toca un sistema externo sin instancia de dev
accesible, declarar el guard mock ahí desde el arranque (mismo criterio
que ya se aplica a `interfaz`/`depende_de` — no dejarlo para un retrofit
posterior). Ver `schemas/plan.contract.md`.

**Encontrado en:** un proyecto real, 2026-08-28 — pedido explícito de
Felipe tras confirmar que el frontend se podía *servir* (nginx responde
200) pero no *usar* de verdad (login necesita LDAP real, otras pantallas
necesitan sistemas externos de solo lectura), ninguno accesible desde el
entorno de desarrollo.

## `.env` real vs. `.env.example` — nunca empaquetar el real

Cada deployable con variables de entorno (`dal/.env`, `backend/.env`, etc.)
mantiene SIEMPRE dos archivos, con roles que no se mezclan:

- **`.env`** — valores reales (locales de dev, o credenciales reales de
  sistemas externos cuando existen, ver "Modo mock" arriba). Vive en
  `.gitignore` siempre. Existe para poder desarrollar y probar de verdad
  contra sistemas reales — no es un artefacto a distribuir.
- **`.env.example`** — mismas variables, incluidas las que sí tienen un
  valor de desarrollo razonable por defecto (puertos, hosts, timeouts),
  pero SIN ningún valor real ni secreto — placeholder tipo `CHANGE_ME` o
  vacío en cada credencial. Este archivo SÍ se versiona y SÍ va en
  cualquier empaquetado/distribución del proyecto (`.zip`, release, etc.)
  — es la única fuente para que quien reciba el proyecto sepa qué
  variables completar, sin heredar ningún secreto ajeno.

**Regla para cualquier tarea de empaquetado/distribución** (zip, release,
tarball): excluir siempre `.env` real de cualquier deployable, incluir
siempre `.env.example`. Mismo criterio que ya aplica un `.gitignore`
normal — un empaquetado que no pasa por git tiene que replicar esa misma
exclusión a mano, no asumirla gratis.

**Encontrado en:** un proyecto real, 2026-08-31 — pedido explícito de
Felipe al pedir un script de empaquetado del proyecto para entregar a
otro equipo: ningún `.env.example` existía todavía pese a que `dal/.env`/
`backend/.env` ya tenían credenciales reales completas.

## `start-local.sh` / `empaquetar.sh` — plantillas maestras, no hand-rolled por proyecto

Arranque sin Docker y empaquetado `.zip` para entrega son necesidades que se repiten
en cualquier proyecto migrado (mismo patrón dal/backend/frontend) — la plantilla vive
UNA sola vez en `Harness/templates/start-local.sh` y `Harness/templates/empaquetar.sh`,
se copia tal cual a la raíz de cada proyecto destino (`chmod +x`), y solo se adapta si
ese proyecto tiene una necesidad genuinamente propia (ej. un chequeo de driver ODBC
de AS400 antes del arranque) — agregada como pieza
aparte, sin tocar la lógica genérica de trap/cleanup ni la exclusión de `.env` real
del empaquetado. Cualquier fix a la lógica genérica (no a un chequeo project-specific)
se aplica primero en `Harness/templates/` y se re-copia a los proyectos existentes —
mismo criterio que ya aplica a `AGENTS.md`/`.agents/rules/` (ver
[[feedback_context_pack_sync_to_master]]).

`start-local.sh` autodetecta qué deployables existen (`dal/`, `backend/`,
`frontend/`) y lanza cada uno en background con su propio venv/`node_modules`
(puertos convención: dal `8001`, backend `8000`, frontend `4200`) — no requiere que
los tres existan. `empaquetar.sh` genera el `.zip` en el directorio padre, excluye
`venv`/`node_modules`/`dist`/`.angular`/`__pycache__`/`.harness`/`.git`/`.env` real, y
verifica después de zipear (no solo antes) que ningún `.env` real haya quedado adentro
y que cada deployable con `.env` real tenga su `.env.example` incluido.

## Lo que este documento NO define — exposición en producción

Cómo el frontend resuelve la URL del backend en `dev`/`qa`/`prod` una vez desplegado
(Cloud Run, GKE), y cómo se autentican esas llamadas, es una decisión que le
corresponde a Operaciones — el equipo con el conocimiento de infraestructura y
despliegue para tomarla bien. **Esto no es una laguna a rellenar por el agente**: es un
límite de responsabilidad puesto a propósito. Si una tarea de despliegue a un ambiente
real requiere esta definición y no está resuelta en el proyecto, el agente debe
señalarlo y preguntar, no asumir un patrón de los usados en local.
