# Flujo: Creación

Construir un proyecto nuevo desde cero (backend FastAPI y/o frontend
Angular) siguiendo la convención fija del harness — sin un monolito origen
del que partir ni código previo que respetar.

`metadata.tipo_flujo` de este flujo es `"creacion"`.

## Estrategia de Planner

Igual de estricto que migración, pero sin las piezas que dependen de un
origen real:

1. **`decisiones_globales` desde cero**: auth, prefijo de API, casing,
   manejo de errores — se deciden aplicando `AGENTS.md` + `.agents/rules/`
   del proyecto destino, igual que en migración (barrer las reglas
   `always_on` de forma sistemática, no confiar en acordarse item por item).
2. **Sin `origen` por item ni `riesgos_heredados[]`**: no hay monolito del
   que heredar nada — se omiten (o quedan `null`/vacíos, ver
   `../../harness-core/schemas/plan.contract.md`).
3. **`schema_bd_origen: null`**: sin BD existente que grondear, el Planner
   decide el tipo de cada campo con el criterio habitual (ver contrato,
   "Modelo canónico").
4. **Misma disciplina de `interfaz`/`depende_de`** que migración — un item
   solo recibe la `interfaz` de sus dependencias declaradas, todo símbolo
   que otro item vaya a importar necesita su línea de import literal.
5. **`knowledge/` antes de `detalle_tecnico`** para librerías no triviales
   — igual que migración, ver `../../harness-core/knowledge/`.
6. **Convención de código**: la fija del harness (mismo mecanismo que
   migración) — creación parte de una base controlada, no hay convención
   previa que heredar como sí exige mantención.

## Ejemplo

Ver `schemas/plan.example.json` — un plan mínimo de 2 items (un endpoint
backend + su consumo desde un servicio Angular) mostrando qué campos
migración-específicos NO aparecen acá.

## Estado

Sin caso real todavía (a diferencia de migración) — la mecánica es la misma
que ya está probada, la diferencia es puramente de qué campos del contrato
aplican. Retomar este README con evidencia real cuando se corra un proyecto
de creación de punta a punta.
