# Angular (core — `@angular/forms`, `@angular/core`, etc; ver `angular-material.md` aparte para componentes de Material)

## `{ provide: LOCALE_ID, useValue: 'es-CL' }` no alcanza solo — hay que registrar los datos del locale, si no cualquier pipe que dependa de él tira `NG02100` en runtime

**Verificado:** 2026-08-26, `@angular/common==22.1.3`, probado con
Playwright contra un browser real (`ng build`/tests con mocks no lo ven).

**Síntoma:** la app compila y carga bien, pero cualquier `number`/
`currency`/`percent` muestra la celda vacía sin excepción visible en
pantalla — solo en consola: `ERROR E: NG02100` (minificado en prod; en
`ng serve` dice completo "Missing locale data for the locale es-CL").

```typescript
// app.config.ts — falta esto antes de declarar LOCALE_ID:
import { registerLocaleData } from '@angular/common';
import localeEsCL from '@angular/common/locales/es-CL';
registerLocaleData(localeEsCL, 'es-CL');
```
Angular solo trae `en-US` por default. Cualquier otro locale existe en
`@angular/common/locales/<locale>` pero no se activa solo con `LOCALE_ID`
— `DecimalPipe`/`CurrencyPipe`/`PercentPipe` tiran en cuanto se usan, no al
arrancar, por eso ni el build ni un test con mocks lo atrapan.

**Encontrado en:** `FE-CORE-003` (`app.config.ts`), 2026-08-26.

## `getRawValue()` de un `FormGroup` con valor inicial `null` sigue tipando ese campo como `T | null`, aunque el control sea `required`

**Verificado:** 2026-08-26, `@angular/core==22.1.0` / `@angular/forms` (misma
versión, paquete del monorepo Angular), código real que pasó `ng build` en
`Web_coas/web-portal-coas-destino`.

**Patrón correcto:**
```typescript
readonly filtro = this.fb.nonNullable.group({
  mes: [null as number | null, Validators.required],
  anio: [null as number | null, Validators.required],
});

buscar(): void {
  if (this.filtro.invalid) {
    return;
  }
  const { mes, anio } = this.filtro.getRawValue();
  if (mes === null || anio === null) {
    return; // TypeScript no sabe que Validators.required ya lo garantizó
  }
  this.reportesService.obtenerDepositos(mes, anio).subscribe({ ... });
}
```

**Patrón incorrecto visto en la práctica:**
```typescript
const { mes, anio } = this.filtro.getRawValue();
this.reportesService.obtenerDepositos(mes, anio).subscribe({ ... }); // TS2345
```
`fb.nonNullable.group(...)` garantiza que el **grupo** nunca sea `null`,
pero el *valor inicial de cada control* sigue siendo el que se declaró — si
se inicializa en `null` (para que un `<mat-select>` arranque vacío en vez
de con una opción precargada), el tipo inferido del campo en
`getRawValue()` sigue siendo `T | null`, sin importar que
`Validators.required` ya haya bloqueado el submit con ese campo vacío.
`ng build` (con `strict: true`, que es el default de `ng new` en este
proyecto) rechaza pasar ese valor a una función que espera `T` no nulo con
`TS2345: Argument of type 'number | null' is not assignable to parameter
of type 'number'` — el chequeo de tipos de TypeScript no razona sobre
`Validators.required` en runtime, solo ve la anotación de tipo del control.

**Regla práctica:** cualquier control inicializado en `null` (para
`<mat-select>`/`<input type="date">` vacíos) necesita un guard explícito
(`if (valor === null) return;`) antes de pasar su valor a una función
tipada sin `null` — no alcanza con la validación reactiva del formulario,
son dos mecanismos distintos (runtime vs. tipos estáticos).

**Encontrado en:** `Web_coas/web-portal-coas-destino`, item `FE-REP-002`
(`reporte-depositos.component.ts`), 2026-08-26.
