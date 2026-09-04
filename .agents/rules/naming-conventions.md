---
description: Convenciones de nombres para archivos, código, servicios y recursos GCP. Aplicar siempre que se cree o renombre un archivo, variable, función, servicio o recurso de infraestructura.
activation: always_on
---

# Convenciones de nombres

## Idioma — decisión transversal

**Todo nombre de dominio va en español**: servicios, componentes, clases de negocio,
variables, mensajes de error y de UI. Ejemplos (dominio ficticio de referencia,
"Pedido"): `ValidaPedido`, `CalculaDescuento`, `pedidoErrorMensaje`.

Las palabras propias del framework/lenguaje se mantienen en inglés porque son parte de
la sintaxis, no del dominio: `service`, `component`, `repository`, `router`, `Create`,
`Update`, `Response`. Ejemplo válido: `FacturaService`, `factura_repository.py`,
`ClienteResponse`. No se traduce el sufijo técnico, se traduce el sustantivo de negocio.

## Python (backend)

- Archivos y módulos: `snake_case.py`
- Clases: `PascalCase`
- Funciones y variables: `snake_case`
- Constantes: `UPPER_SNAKE_CASE`
- **Nombre de archivo y de clase deben usar la misma convención de casing** — no mezclar
  `<entidad>Api.py` (camelCase) con `<entidad>_repository.py` (snake_case) dentro del
  mismo proyecto, como se observó en un prototipo interno. Todo archivo Python va en
  `snake_case.py`, sin excepción.
- Routers agrupados por dominio, un archivo por entidad (`app/api/factura.py`), no todo
  en `main.py`
- Capas del proyecto (ver `backend-architecture.md` para el detalle de responsabilidades):
  `app/api/` (routers) → `app/service/` → `app/repository/` → `app/model/`
- Schemas Pydantic separados por dirección: `app/schema/request/` y
  `app/schema/response/`. Un mismo schema **no** se reutiliza para input y output; se
  nombra según su rol: `FacturaCreate`, `FacturaResponse`, `FacturaUpdate`

## Angular (frontend)

- Componentes: carpeta y archivos en `kebab-case` (`confirmacion-pedido/confirmacion-pedido.ts`,
  `.html`, `.css`, `.spec.ts`), clase en `PascalCase` (`ConfirmacionPedido`). Selector
  siempre `app-<nombre>`.
- Servicios: carpeta `PascalCase` con el nombre del dominio (`ValidaPedido/`), y
  dentro el archivo en `kebab-case.service.ts` (`valida-pedido.service.ts`), la
  clase con sufijo `Service`.
- Un servicio por integración/responsabilidad — no un service genérico que hace de todo.
- Ver `frontend-angular.md` para dónde vive cada tipo de archivo (`page/` vs
  `components/` vs `services/`).

## Recursos GCP

- Formato general: `<sistema>-<ambiente>-<recurso>`, ej. `facturacion-prod-cloudrun`,
  `facturacion-dev-sa` (service account)
- Ambientes válidos: `dev`, `qa`, `prod` — nunca abreviaturas ambiguas como `p` o `d`
- Service accounts con propósito único: una SA por servicio, no una SA compartida entre
  múltiples aplicaciones. Patrón observado y válido:
  `<project_name>@<project_id>.iam.gserviceaccount.com`
- Buckets, topics de Pub/Sub y otros recursos con nombre que identifique el sistema
  dueño, no nombres genéricos como `data` o `temp`

## Commits y ramas

- Ramas: `feature/<descripcion-corta>`, `fix/<descripcion-corta>`
- Commits siguiendo Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`) para
  mantener trazabilidad en migraciones
