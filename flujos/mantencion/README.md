# Flujo: Mantención

Modificar o extender un módulo/proyecto que ya existe y ya está en
producción (o al menos ya versionado en git) — un cambio puntual, no una
migración ni una construcción desde cero.

`metadata.tipo_flujo` de este flujo es `"mantencion"`.

## Principio: scope más chico, mismo rigor

"Más laxo" en mantención se refiere solo al **tamaño** del cambio (menos
archivos, menos pasos por item) — nunca al nivel de exigencia. El gate de
Compliance + checks determinísticos es tan estricto como en los otros dos
flujos; de hecho mantención agrega DOS chequeos que los otros flujos no
tienen (ver `../../harness-core/schemas/plan.contract.md`, secciones
"Convention check" y "Regression check"):

- **Convención relativa, no fija**: si el archivo que el item toca ya está
  en camelCase, lo nuevo que Executor agregue también debe quedar en
  camelCase — no la convención por defecto del harness.
- **Regresión de la suite existente**: los tests que el deployable YA
  TENÍA antes de este item deben seguir pasando, no solo los
  `tests_requeridos` propios del item.

## Estrategia de Planner

1. **Scope mínimo por item**: a diferencia de migración/creación (que
   pueden tener items de varios archivos), un item de mantención debería
   tocar el menor número de archivos posible — el blast radius de un
   cambio en un proyecto grande no es obvio a simple vista, y un item chico
   es más fácil de verificar (Compliance + regression check) que uno
   grande.
2. **`detalle_tecnico` referencia el archivo/módulo existente**: no hay
   `item.origen` (eso es de migración) — en su lugar, `detalle_tecnico`
   debe indicar explícitamente qué archivo(s) existentes hay que leer/
   seguir como referencia de convención y de patrones ya usados en ese
   módulo (imports, forma de manejar errores, estilo de nombres).
3. **El proyecto destino debe ser un repo git ya trackeado** — el
   convention check reconstruye la versión "antes" del archivo vía
   `git show HEAD:<ruta>`; un archivo sin ese historial (fuera de un repo
   git, o no commiteado todavía) no tiene convención previa que heredar y
   ese chequeo se salta para él (no bloquea, pero tampoco protege).
4. **Sin `origen`/`riesgos_heredados[]`/`schema_bd_origen`** — mantención no
   tiene un "monolito origen" del que heredar nada; el proyecto destino ES
   la fuente de verdad ya vigente.
5. **`tests_requeridos` sigue existiendo igual que en los otros flujos** —
   el regression check es ADICIONAL, no un reemplazo del smoke test del
   item.
6. **`tests_requeridos` debe cubrir el FLUJO FUNCIONAL COMPLETO al que
   pertenece el cambio, no solo la pieza tocada.** Ejemplo: un item que
   cambia el algoritmo de hash del login debe declarar tests de login
   exitoso, credenciales inválidas, y verificación del hash — no solo un
   test de la función de hash aislada — aunque `archivos_destino`/
   `detalle_tecnico` del item sigan acotados al cambio real (regla 1,
   scope mínimo). El regression check (arriba) protege lo que YA existía;
   esta regla asegura que lo NUEVO tenga cobertura real del comportamiento
   del que forma parte, no solo de la línea que cambió.
7. **Hallazgos fuera de alcance se reportan, nunca se corrigen solos** — si
   Executor/Compliance notan un bug o una mejora posible fuera del item
   mientras trabajan, el mecanismo automático los vuelca en
   `<deployable>/docs/riesgos_heredados.md`/`recomendaciones-tecnicas.md`
   (ver `../../harness-core/schemas/plan.contract.md`, "Hallazgos fuera de
   alcance") — no hace falta que el Planner haga nada para que esto pase,
   pero sí conviene revisar esos archivos entre tandas de items, no solo al
   cerrar el proyecto.

## Ejemplo

Ver `schemas/plan.example.json` — un item mínimo que agrega una función a un
archivo backend existente, referenciándolo en `detalle_tecnico`.

## Estado

Sin caso real todavía. El diseño de `convention_check.py`/
`regression_check.py` está acotado a propósito (ver contrato: solo casing de
identificadores, solo backend/pytest) — ampliar alcance cuando aparezca
evidencia real de que hace falta, no antes.
