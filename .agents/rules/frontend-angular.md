---
description: Convenciones de estructura y arquitectura para frontend Angular. Aplicar al generar o modificar componentes, servicios, o estructura de proyecto Angular.
activation: always_on
---

# Frontend — Angular

## Base: bootstrap explícito, sin depender de un template interno

Todo proyecto Angular nuevo se genera con:

```
ng new <nombre> --routing --style=scss --ssr=false --zoneless --standalone --strict --file-name-style-guide=2016 --skip-git
```

y se configura manualmente (no se copia un proyecto legado existente) hasta llegar a
este estado:

- **Angular** (última versión estable disponible al bootstrapear — verificado en la
  práctica con Angular 22.1.x / CLI 22.1.5), componentes standalone, zoneless vía el
  flag `--zoneless` de `ng new` de arriba — **esta versión de la CLI ya no genera ni
  necesita `provideZonelessChangeDetection()` a mano en `app.config.ts`** (confirmado
  con un `ng build` real: sin `zone.js` en `package.json` ni en polyfills, sin ese
  provider explícito, el build compila limpio). Esto corrige una instrucción anterior
  de esta misma regla que asumía el paso manual — si en el futuro el CLI disponible es
  una versión que no soporta `--zoneless` nativo, sí hay que agregarlo a mano; no
  asumir un caso u otro sin confirmarlo con un build real. Sin `NgModule`.
- `--file-name-style-guide=2016` mantiene el sufijo explícito en nombres de archivo
  (`algo.component.ts`, `algo.service.ts`) en vez del estilo concho 2025 (`algo.ts`) —
  consistente con la convención de sufijo que esta misma regla ya pide para servicios
  más abajo.
- `--skip-git` evita que `ng new` inicialice un repositorio git separado dentro de
  `frontend/` cuando el resto del proyecto destino no está versionado — si el proyecto
  destino sí tiene git en la raíz, evaluar si conviene igual (probablemente sí, para no
  fragmentar el historial).
- **Signals nativos** (`signal()`) para estado local — no se introduce NgRx, Akita ni
  otro state manager salvo que el proyecto lo requiera explícitamente y se documente por qué
- SSR **no** se genera por defecto (`--ssr=false` arriba); si el proyecto lo necesita, se
  agrega explícitamente `@angular/ssr` + Express después (ver sección de deploy). Sin
  esa necesidad concreta, se despliega solo el bundle browser detrás de Nginx
- Sin librería de UI preinstalada (Material, PrimeNG, Tailwind) — se agrega solo si el
  proyecto la necesita, no por defecto
- `src/app/app.routes.ts` empieza vacío a propósito — completar rutas es trabajo del
  proyecto

**No se inventan carpetas de primer nivel** (`core/`, `shared/`, `store/`, `features/`)
sin necesidad concreta. Arrancar minimalista es intencional.

## Estructura recomendada cuando el proyecto crece

Para proyectos pequeños (el perfil típico de esta migración), una vez que el proyecto
supera el componente `App` inicial, seguir la estructura plana validada en proyectos
reales — **no** modularización por feature ni lazy loading, salvo que el proyecto
realmente lo justifique por tamaño:

```
src/app/
├── page/          # componentes de RUTA (vistas top-level)
├── components/    # componentes de UI reutilizables, sin ruta propia
└── services/      # un servicio por integración/responsabilidad
```

Antes de crear un componente o servicio nuevo, revisar si ya existe algo equivalente en
`components/` o `services/` para no duplicar.

## `app.routes.ts` es un item de CIERRE, no de arranque

Mismo principio que `main.py` en el backend (monta routers ya generados) —
`app.routes.ts` monta componentes de página ya generados, así que depende
de TODOS ellos, no solo de los servicios base (`Auth`, interceptors, etc.).
Planificarlo como item temprano rompe `ng build` del proyecto ENTERO
(Executor no tiene el import path real de cada página y adivina la ruta),
lo que bloquea en cascada a cualquier otro item frontend en curso — cada
componente que otro item vaya a montar (via `loadComponent`, `routerLink`
directo a un componente, etc.) tiene que declarar su nombre de clase +
import path exacto en su propia `interfaz.dependencia_reusable`, mismo
criterio que ya se usa para exponer un router de FastAPI (ver
`Harness/schemas/plan.contract.md`, sección "Items 'ensambladores'...").

## Naming (ver también `naming-conventions.md`)

- Componente: carpeta y archivos en `kebab-case`, clase en `PascalCase`, selector
  `app-<nombre>`, en español (`confirmacion-pedido/`, clase `ConfirmacionPedido`)
- Servicio: carpeta `PascalCase` con el dominio (`ValidaPedido/`), archivo
  `kebab-case.service.ts`, clase con sufijo `Service`

## HTTP

- Usar siempre `HttpClient` de `@angular/common/http`. No importar el `HttpModule`
  legado de `@angular/http` bajo ninguna circunstancia en código nuevo.
- Manejo de errores de HTTP centralizado vía `HttpInterceptor`, no repetido en cada
  servicio (a diferencia de un patrón heredado observado en proyectos legados, donde
  cada servicio maneja su propio header/token manualmente — eso es deuda técnica, no
  el estándar).

## Seguridad — esto anula cualquier patrón heredado

Ningún `environment.*.ts` lleva API keys, tokens u otras credenciales hardcodeadas,
aunque existan proyectos legados donde esto ocurre (ver `security-baseline.md`). Los
Los `environment.*.ts` que se creen solo deben llevar configuración no sensible
(flags, URLs base); cualquier secreto real se resuelve en runtime vía el backend o
Secret Manager, nunca embebido en el bundle del frontend.

## Build y ambientes

- Cuatro configuraciones de build estándar: `production` (default), `qa`, `dev`,
  `development` (sin optimizar, para `ng serve`/`watch`). `ng new` solo genera
  `environment.ts`/`environment.development.ts` — crear a mano los cuatro archivos
  `environment.<config>.ts` y registrar cada configuración en `angular.json` vía
  `fileReplacements`.
- **Antes de dar por buena una configuración de ambiente, verificar que el contenido de
  cada `environment.*.ts` sea realmente distinto entre sí** — es fácil copiar/pegar el
  mismo contenido placeholder en los cuatro al crearlos a mano; cada uno debe reflejar
  su ambiente real, no quedar igual a los demás.

## Deploy — verificar consistencia de puertos

Hay dos mecanismos de despliegue que deben mantenerse alineados manualmente:

1. **Nginx estático** (`Dockerfile` + `default.conf`): sirve el bundle browser
   compilado, sin usar el servidor SSR/Express aunque esté configurado.
2. **Cloud Run / Knative** (`deploy/<ambiente>/run/service-cloud-run.yaml`): espera que
   el contenedor escuche en un puerto específico (`containerPort`).

**El agente debe verificar explícitamente que el puerto expuesto por Nginx/Dockerfile
coincida con el `containerPort` del manifiesto de Cloud Run antes de dar un despliegue
por terminado.** Esto no es una suposición: ya se detectó un desalineamiento de este
tipo en un proyecto real (Nginx en 80, manifiesto esperando 8080) por escribir ambos
archivos a mano sin verificarlos entre sí, así que no se debe asumir que quedan
sincronizados solo por escribirlos "razonablemente".

Si el proyecto necesita SSR real en producción, el despliegue debe apuntar al servidor
Express (`server.ts`), no al bundle estático de Nginx — son mecanismos mutuamente
excluyentes, no complementarios.

## Conexión con el backend

- **Desarrollo local:** ver `local-development.md` — ejecución separada (con CORS) o
  docker-compose con Nginx como reverse proxy (sin CORS).
- **Producción:** cómo se resuelve la URL del backend y la autenticación entre servicios
  es responsabilidad de Operaciones, no de este documento. El agente no debe inventar un
  mecanismo por su cuenta (ej. hardcodear una URL, asumir un esquema de auth) — si una
  tarea de despliegue lo requiere y no hay instrucción explícita, debe preguntarlo.

## Español neutro en todo texto de UI — sin voseo argentino

Todo texto que el usuario final lee (labels, botones, errores, placeholders,
tooltips) va en **español neutro**, nunca voseo rioplatense (`sos`, `tenés`,
`podés`, `hacé`, `dale`, `acá` sin tilde). Forma neutra sin pronombre
explícito ("Ingresa aquí") o tercera persona — nunca conjugación voseante.

**Bug real:** un login real (cliente en Chile) generó `¿Sos
administrador? Ingresa aca` — corregido a `¿Eres administrador? Ingresa
aquí`. Ni `ng build` ni tests lo detectan (texto plano en un template),
solo se ve probando la UI real.
