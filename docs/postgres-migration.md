# PostgreSQL migration runbook

The Render service must use PostgreSQL before it is resumed. Local SQLite
remains available only for development when durable storage is not required.

## Safety properties

- Connection details are read only from the process environment.
- The migration never prints records, usernames, IDs, messages, or connection details.
- The source database is opened read-only.
- All target writes and validation run in one PostgreSQL transaction.
- Re-running the same snapshot is idempotent.
- Every table is validated by exact row count and a deterministic checksum.
- Any mismatch rolls back the complete migration.

## Schema mapping

The PostgreSQL backend preserves the four existing tables and their unique
constraints. Discord identifiers remain text. XP and message counters use
64-bit integers. New timestamps are stored in UTC.

Legacy timestamps do not include an offset. The migration defaults to
Asia/Taipei for those values and converts them to UTC. Use
--source-timezone if the snapshot was written in another timezone.

## Migration procedure

1. Keep the production Bot suspended so no new SQLite writes occur.
2. Obtain a trustworthy production SQLite snapshot. If no snapshot survived
   the earlier redeploy, the migration cannot reconstruct missing records.
3. Create a Render PostgreSQL instance in the same region as the service.
4. Configure the service's DATABASE_URL secret using Render's managed
   connection value. Never paste it into source code, documentation, logs, or
   a pull request.
5. Validate the snapshot without connecting to PostgreSQL:

       python scripts/migrate_sqlite_to_postgres.py --source <snapshot-path> --dry-run

6. Apply the atomic migration from a trusted administrative environment:

       python scripts/migrate_sqlite_to_postgres.py --source <snapshot-path> --apply

7. Confirm all four table counts are reported as verified.
8. Deploy the branch to an isolated preview or staging service first.
9. Verify /live, /health, member joins, XP updates, leaderboards, settings,
   and a clean SIGTERM shutdown.
10. Only after those checks pass, deploy and resume the production service.

## Rollback

Do not switch a live service back to SQLite after PostgreSQL accepts new
writes. Roll back the application version while retaining PostgreSQL as the
source of truth. Preserve the original SQLite snapshot read-only until the
post-migration verification window is complete.
