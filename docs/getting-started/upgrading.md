# Upgrading

!!! note "Placeholder"
    This page hasn't been written yet. It will cover the following.

How to upgrade safely: take a backup, pull the new version, run the database migrations (`alembic upgrade head`, which the API container runs on start), restart, check it works, and roll back if it doesn't.
