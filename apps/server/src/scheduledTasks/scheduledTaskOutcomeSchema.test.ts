import { assert, it } from "@effect/vitest";
import * as NodeSqliteClient from "@t3tools/shared/nodeSqliteClient";
import * as Effect from "effect/Effect";
import * as SqlClient from "effect/sql/SqlClient";

import { migrationManifest, runMigrations } from "../persistence/Migrations.ts";
import { ensureScheduledTaskOutcomeSchema } from "./scheduledTaskOutcomeSchema.ts";

const insertTask = (status: string) =>
  Effect.gen(function* () {
    const sql = yield* SqlClient.SqlClient;
    yield* sql`INSERT INTO scheduled_tasks ${sql.insert({
      task_id: status,
      title: "task",
      prompt: "Run",
      enabled: 1,
      schedule_json: '{"type":"interval","everyMs":60000}',
      project_id: "project",
      thread_id: null,
      workspace_strategy_json: '{"type":"root"}',
      model_selection_json: '{"instanceId":"codex","model":"gpt-5"}',
      runtime_mode: "full-access",
      interaction_mode: "default",
      created_by: "user",
      creation_source: "web",
      created_at: "2026-10-01T00:00:00.000Z",
      updated_at: "2026-10-01T00:00:00.000Z",
      next_run_at: "2026-10-02T00:00:00.000Z",
      last_run_at: "2026-10-01T00:00:00.000Z",
      last_run_status: status,
      last_run_error: null,
      run_count: 1,
    })}`;
  });

const outcomeColumns = Effect.gen(function* () {
  const sql = yield* SqlClient.SqlClient;
  const columns = yield* sql<{ name: string }>`PRAGMA table_info(scheduled_tasks)`;
  return columns
    .map((column) => column.name)
    .filter((name) => ["last_run_message_id", "last_run_thread_id", "last_run_id"].includes(name));
});

const freshDatabase = NodeSqliteClient.layer({ filename: ":memory:" });

it.effect("adds the outcome schema to a fresh database without recording a migration", () =>
  Effect.gen(function* () {
    const sql = yield* SqlClient.SqlClient;
    yield* runMigrations();
    yield* ensureScheduledTaskOutcomeSchema();
    assert.deepEqual(yield* outcomeColumns, [
      "last_run_message_id",
      "last_run_thread_id",
      "last_run_id",
    ]);
    const [index] = yield* sql<{ name: string }>`
      SELECT name FROM sqlite_master WHERE name = 'scheduled_tasks_outcome_idx'`;
    assert.equal(index?.name, "scheduled_tasks_outcome_idx");
    const [head] = yield* sql<{
      id: number;
    }>`SELECT MAX(migration_id) AS id FROM effect_sql_migrations`;
    assert.equal(head!.id, Math.max(...migrationManifest.map(([id]) => id)));
  }).pipe(Effect.provide(freshDatabase)),
);

it.effect("marks a stock database's historical successes unverified and keeps the rest", () =>
  Effect.gen(function* () {
    const sql = yield* SqlClient.SqlClient;
    yield* runMigrations();
    for (const status of ["never", "running", "succeeded", "failed"]) yield* insertTask(status);
    yield* ensureScheduledTaskOutcomeSchema();
    const rows = yield* sql<{
      task_id: string;
      last_run_status: string;
      last_run_error: string | null;
      next_run_at: string;
      run_count: number;
    }>`SELECT task_id, last_run_status, last_run_error, next_run_at, run_count FROM scheduled_tasks`;
    assert.equal(rows.length, 4);
    for (const row of rows) {
      assert.equal(row.last_run_status, row.task_id === "succeeded" ? "unknown" : row.task_id);
      assert.equal(row.next_run_at, "2026-10-02T00:00:00.000Z");
      assert.equal(row.run_count, 1);
    }
    assert.include(
      rows.find((row) => row.task_id === "succeeded")!.last_run_error!,
      "dispatch only",
    );
  }).pipe(Effect.provide(freshDatabase)),
);

it.effect("running twice, or on a database that already has the columns, changes nothing new", () =>
  Effect.gen(function* () {
    const sql = yield* SqlClient.SqlClient;
    yield* runMigrations();
    // A database written by the earlier numbered-migration build already has one column.
    yield* sql`ALTER TABLE scheduled_tasks ADD COLUMN last_run_message_id TEXT`;
    yield* ensureScheduledTaskOutcomeSchema();
    yield* insertTask("succeeded");
    yield* sql`UPDATE scheduled_tasks SET last_run_message_id = 'message' WHERE task_id = 'succeeded'`;
    yield* ensureScheduledTaskOutcomeSchema();
    assert.deepEqual(yield* outcomeColumns, [
      "last_run_message_id",
      "last_run_thread_id",
      "last_run_id",
    ]);
    const [row] = yield* sql<{ last_run_status: string }>`
      SELECT last_run_status FROM scheduled_tasks WHERE task_id = 'succeeded'`;
    assert.equal(row!.last_run_status, "succeeded");
  }).pipe(Effect.provide(freshDatabase)),
);
