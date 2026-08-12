# Multi-schema tenancy (Swotify-style)

Prism uses **one Postgres database** with **one schema per organization** when `DATABASE_URL` starts with `postgresql`.

## Mental model

```text
public.institutions     ← registry (code, name, schema_name)
public.system_initialization

tenant_acme.users       ← org Acme data
tenant_acme.centers
tenant_acme.students …

tenant_nova.users       ← org Nova data
…
```

## Request flow

1. Login: `institutionCode` → `public.institutions` → `schema_name`
2. Auth against `{schema}.users`
3. JWT: `institution_id`, `sub`, `role`
4. Authenticated requests: `get_db()` reads JWT → binds `schema_translate_map`

## Key files

| File | Role |
|------|------|
| `app/services/tenant_context.py` | Schema resolution, ContextVar |
| `app/db/tenant.py` | `create_tenant_schema()`, public table list |
| `app/core/deps.py` | `get_db()` tenant binding, `get_public_db()` |
| `app/services/setup.py` | Provision org + schema |

## SQLite dev

When using `sqlite:///`, multi-schema is **disabled** — all tables stay in `public` (tests and local dev).

## Provisioning a new organization

1. `POST /api/v1/setup` (first org only, marks deployment initialized)
2. Future: `POST /api/v1/organizations` to add more tenants (platform admin)

## Rules

- Registry always in `public.institutions`
- Never trust client-supplied schema names — resolve from registry
- Same email allowed in different tenant schemas
- Migrations: patch `public` + loop `SELECT schema_name FROM public.institutions`
