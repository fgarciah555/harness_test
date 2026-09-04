# Base de conocimiento — Angular Material

## `MatSelect`/`MatOption` viven en `@angular/material/select`, no en `form-field`

**Verificado:** 2026-08-26, `@angular/material==22.1.4`, código real que
compiló con `ng build` en `Web_coas/web-portal-coas-destino`.

**Patrón correcto:**
```typescript
import { MatFormField, MatLabel } from '@angular/material/form-field';
import { MatSelect, MatOption } from '@angular/material/select';
```

**Patrón incorrecto visto en la práctica:**
```typescript
import { MatFormField, MatLabel, MatSelect, MatOption } from '@angular/material/form-field';
```
`@angular/material/form-field` no exporta `MatSelect` ni `MatOption` — TS2305,
`ng build` no compila. Mismo tipo de error ya documentado para `MatInput`
(vive en `@angular/material/input`) y `MatIconButton`
(vive en `@angular/material/button`) más abajo en este archivo — cada
control de Angular Material tiene su propio subpath, `form-field` solo
exporta el wrapper (`MatFormField`/`MatLabel`/`MatError`/`MatHint`), nunca
los controles que van adentro.

**Encontrado en:** `Web_coas/web-portal-coas-destino`, item `FE-REP-003`
(`reporte-venta-diaria.component.ts`), 2026-08-26.

Ver `README.md` para el formato y la disciplina completa. Componentes ya
usados y verificados con `ng build` real en `web-portal-coas` (Login,
`COAS-FE-LOGIN-001`): `MatCard`/`MatCardContent` (`@angular/material/card`),
`MatFormField`/`MatLabel`/`MatError` (`@angular/material/form-field`),
`MatInput` (`@angular/material/input` — **no** se exporta desde
`form-field`, submodule aparte), `MatButton` (`@angular/material/button`),
`MatProgressSpinner` (`@angular/material/progress-spinner`). No repetido acá
por brevedad, sigue vigente.

## Toolbar / icon / icon-button para pantallas de menú

**Verificado:** 2026-08-22, `@angular/material==22.1.3` — vía
`types/<paquete>.d.ts` real dentro de
`frontend/node_modules/@angular/material/` (no vía WebFetch a la doc
oficial, que sirve la SPA sin contenido navegable; leer el `.d.ts` real
instalado es más confiable que la doc pública cuando el paquete ya está en
el proyecto).

**Patrón correcto:**
```typescript
import { MatToolbar, MatToolbarRow } from '@angular/material/toolbar'; // MatToolbarRow solo si el toolbar tiene más de una fila
import { MatIcon } from '@angular/material/icon';
import { MatButton, MatIconButton } from '@angular/material/button'; // MatIconButton vive en el mismo subpath que MatButton, no en icon
```

`<mat-icon>` con nombre de ligadura (ej. `<mat-icon>logout</mat-icon>`)
funciona sin registrar SVGs porque `frontend/src/index.html` ya carga la
fuente de Material Icons
(`https://fonts.googleapis.com/icon?family=Material+Icons`, agregada por
`ng add @angular/material` al bootstrapear el proyecto) — no hace falta
`MatIconRegistry` para íconos por nombre de ligadura.

**Patrón incorrecto a evitar:** importar `MatIconButton` desde
`@angular/material/icon` (no está ahí, es un botón — vive junto a
`MatButton` en `@angular/material/button`).

**Encontrado en:** `web-portal-coas`, planificación de
`COAS-FE-MENU-001`/`COAS-FE-MENU-002`, 2026-08-22 (verificado antes de
escribir el item, sin fallo previo que lo motivara).

## Botón submit: deshabilitar por carga Y por validez del form, no solo por carga

**Patrón correcto:**
```html
<button mat-button type="submit" [disabled]="cargando() || formulario.invalid">
```

**Patrón incorrecto visto en la práctica:** `[disabled]="cargando()"` solo —
compila y funciona mientras se envía la request, pero el botón queda
habilitado con un formulario inválido (el usuario puede hacer submit antes
de completar campos requeridos). Las dos condiciones son independientes,
ninguna reemplaza a la otra.

**Encontrado en:** `web-portal-coas-migrado`, `COAS-AUTH-002`, 2026-08-24.
