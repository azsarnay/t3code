import { assert, it } from "@effect/vitest";
import * as NodeSqliteClient from "@t3tools/shared/nodeSqliteClient";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";
import * as SqlClient from "effect/sql/SqlClient";

import { runMigrations } from "../Migrations.ts";

const layer = it.layer(Layer.mergeAll(NodeSqliteClient.layer({ filename: ":memory:" })));

layer("077_ScheduledTaskOutcomes", (it) => {
  it.effect("preserves schedules and marks historical dispatch success as unverified", () =>
    Effect.gen(function* () {
      const sql = yield* SqlClient.SqlClient;
      yield* runMigrations({ toMigrationInclusive: 76 });
      for (const status of ["never", "running", "succeeded", "failed"]) {
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
      }
      yield* runMigrations({ toMigrationInclusive: 77 });
      const rows = yield* sql<{
        task_id: string;
        last_run_status: string;
        last_run_error: string | null;
        last_run_message_id: string | null;
        last_run_thread_id: string | null;
        last_run_id: string | null;
        next_run_at: string;
        run_count: number;
      }>`SELECT task_id, last_run_status, last_run_error, last_run_message_id,
        last_run_thread_id, last_run_id, next_run_at, run_count FROM scheduled_tasks`;
      assert.equal(rows.length, 4);
      for (const row of rows) {
        assert.equal(row.last_run_status, row.task_id === "succeeded" ? "unknown" : row.task_id);
        assert.equal(row.last_run_message_id, null);
        assert.equal(row.last_run_thread_id, null);
        assert.equal(row.last_run_id, null);
        assert.equal(row.next_run_at, "2026-10-02T00:00:00.000Z");
        assert.equal(row.run_count, 1);
      }
      assert.include(
        rows.find((row) => row.task_id === "succeeded")!.last_run_error!,
        "dispatch only",
      );
    }),
  );

  it.effect("skips columns a database written by the earlier migration 70 build already has", () =>
    Effect.gen(function* () {
      const sql = yield* SqlClient.SqlClient;
      yield* sql`DELETE FROM effect_sql_migrations WHERE migration_id = 77`;
      yield* runMigrations({ toMigrationInclusive: 77 });
      const columns = yield* sql<{ name: string }>`PRAGMA table_info(scheduled_tasks)`;
      assert.equal(columns.filter((column) => column.name === "last_run_id").length, 1);
    }),
  );
});
