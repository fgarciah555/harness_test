# pydantic-settings

## Override de `env_file` en tiempo de instancia (para tests con `.env` alternativo)

**Verificado:** 2026-08-21, `pydantic-settings` instalado vía `pip install
pydantic-settings` sin fijar versión. Confirmado con un snippet ejecutado de
verdad, no solo leído de documentación.

**Patrón correcto:**
```python
class Settings(BaseSettings):
    database_url: str
    model_config = SettingsConfigDict(env_file=".env")

def get_settings(env_path: str | None = None) -> Settings:
    return Settings(_env_file=env_path) if env_path else Settings()
```
El kwarg de instancia es `_env_file` **con guion bajo** — es un parámetro
especial de `BaseSettings.__init__`, no un campo del modelo.

**Patrón incorrecto visto en la práctica:**
```python
Settings(env_file=env_path)          # sin guion bajo
Settings.model_config = SettingsConfigDict(env_file=env_path); Settings()  # mutar estado de clase compartido
```
El primero, sin guion bajo, se trata como un campo desconocido: con
`extra="ignore"` (default en algunas configuraciones) se ignora en silencio y
el override nunca aplica; con `extra="forbid"` explícito lanza
`ValidationError: env_file Extra inputs are not permitted`. El segundo
"funciona" pero muta un atributo de clase compartido entre todas las
instancias — fragante ante concurrencia y cacheo (`lru_cache`), y fue
rechazado por Compliance como no verificable/poco confiable aunque pasaba los
tests existentes en ese momento (ver más abajo, los tests originales no
cubrían este caso).

**Encontrado en:** 2026-08-21, un item de configuración base —
Compliance rechazó la mutación de clase compartida; el reintento generó la
variante sin guion bajo, que además rompía en runtime (confirmado corriendo
el smoke test); la tercera vuelta, con el patrón correcto ya documentado en
`detalle_tecnico`, pasó.

## Campos requeridos sin default en tests — no es un bug del código generado

**Contexto (no es un patrón de librería per se, es un recordatorio de
disciplina de Planner):** si `Settings` tiene campos sin default (ej.
`database_url: str`, `secret_key: str`, `cors_origins: list[str]`), un test
que solo hace `monkeypatch.setenv('DATABASE_URL', ...)` y llama
`get_settings()` va a fallar con `ValidationError: secret_key Field
required` — **no porque el código generado esté mal**, sino porque el propio
test del Planner no seteó todas las variables obligatorias. Pasó 2 veces en
items de configuración/login distintos, y en una tercera variante
estructural donde `main.py` evalúa `get_settings()` a nivel de módulo — ahí
ni monkeypatch alcanza, porque el import pasa en tiempo de *collection* de
pytest antes de que corra cualquier fixture; la solución ahí fue un
`backend/.env` real de desarrollo, no un monkeypatch).

**Regla para el Planner al escribir `tests_requeridos` que toquen
`Settings`:** setear SIEMPRE las tres variables obligatorias
(`DATABASE_URL`, `SECRET_KEY`, `CORS_ORIGINS` — o las que el proyecto
concreto haya definido sin default), no solo la que el test específicamente
quiere ejercitar.
