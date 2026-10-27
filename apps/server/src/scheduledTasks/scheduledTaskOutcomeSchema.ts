import * as Effect from "effect/Effect";
import * as SqlClient from "effect/sql/SqlClient";

/**
 * Adds the run-outcome columns at startup instead of as a numbered migration, so the database's
 * migration history stays identical to stock and stock builds can open it. Safe on every start:
 * columns and the index are added only when missing, and only rows stock wrote (`succeeded` with
 * no `last_run_message_id`) are marked unverified. This build always records a message id.
 */
export const ensureScheduledTaskOutcomeSchema = Effect.fn("ensureScheduledTaskOutcomeSchema")(
  function* () {
    const sql = yield* SqlClient.SqlClient;
    const columns = yield* sql<{ readonly name: string }>`PRAGMA table_info(scheduled_tasks)`;
    const has = (name: string) => columns.some((column) => column.name === name);
    if (!has("last_run_message_id")) {
      yield* sql`ALTER TABLE scheduled_tasks ADD COLUMN last_run_message_id TEXT`;
    }
    if (!has("last_run_thread_id")) {
      yield* sql`ALTER TABLE scheduled_tasks ADD COLUMN last_run_thread_id TEXT`;
    }
    if (!has("last_run_id")) {
      yield* sql`ALTER TABLE scheduled_tasks ADD COLUMN last_run_id TEXT`;
    }
    yield* sql`UPDATE scheduled_tasks SET last_run_status = 'unknown',
      last_run_error = 'This historical result confirmed dispatch only; the destination run was not verified.'
      WHERE last_run_status = 'succeeded' AND last_run_message_id IS NULL`;
    yield* sql`CREATE INDEX IF NOT EXISTS scheduled_tasks_outcome_idx ON scheduled_tasks(last_run_status)
      WHERE last_run_message_id IS NOT NULL`;
  },
);
