# API endpoints — monolito-pedidos

> Generado automáticamente por el harness a partir de los items `backend`
> aprobados en `.harness/config/plan.json`. No editar a mano — se
> reescribe completo cada vez que Compliance aprueba un nuevo item backend.

## POST /api/v1/auth/login

- **Origen:** `PED-001`
- **Request:**
  ```json
  { "email": "string", "password": "string" }
  ```
- **Response 200:**
  ```json
  { "accessToken": "string (JWT)", "refreshToken": "string" }
  ```

## GET /api/v1/pedidos

- **Origen:** `PED-002`
- **Headers:** `Authorization: Bearer <accessToken>`
- **Response 200:**
  ```json
  [{ "id": "string", "fecha": "string (ISO 8601)", "estado": "string", "total": "number" }]
  ```

## POST /api/v1/pedidos

- **Origen:** `PED-003`
- **Headers:** `Authorization: Bearer <accessToken>`
- **Request:**
  ```json
  { "items": "array", "nota": "string, opcional, máx 500 caracteres" }
  ```
- **Response 201:**
  ```json
  { "id": "string", "fecha": "string (ISO 8601)", "estado": "string", "total": "number" }
  ```
