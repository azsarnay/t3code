import * as Effect from "effect/Effect";
import * as SqlClient from "effect/sql/SqlClient";

export default Effect.gen(function* () {
  const sql = yield* SqlClient.SqlClient;
  // Databases written by an earlier build of this change (as migration 70) already have these.
  const columns = yield* sql<{ readonly name: string }>`
    PRAGMA table_info(scheduled_tasks)
  `;
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
    WHERE last_run_status = 'succeeded'`;
  yield* sql`CREATE INDEX IF NOT EXISTS scheduled_tasks_outcome_idx ON scheduled_tasks(last_run_status)
    WHERE last_run_message_id IS NOT NULL`;
});
