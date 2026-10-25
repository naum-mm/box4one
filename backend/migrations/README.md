# Telegram migration 001

Run `python -m app.telegram_migrate` inside the **new backend image** as an
explicit one-off operation before enabling Telegram. No migration runs at startup.
The migration creates only `telegram_users`, `telegram_requests` and `telegram_runtime`, using
SQLAlchemy `Table.create(checkfirst=True)`, then reconciles enabled users with
`TELEGRAM_ALLOWED_USERS`. Telegram IDs and message IDs are BIGINT; foreign keys
reference existing jobs without changing them. A unique (user, message) constraint
and a unique job relation enforce idempotency. Run twice to verify repeatability.

Back up MariaDB first. Roll back by disabling the profile and internal token;
retain additive tables and existing job records. Do not drop jobs/files or restore
a stale database over newer jobs. Serialize migration invocations.
