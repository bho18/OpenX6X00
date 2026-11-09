// SPDX-License-Identifier: LGPL-2.1-or-later
/* Execute the real numeric and named migrations against independent copies of
 * the shipped defaults. Failures are injected by SQLite triggers, not by a
 * duplicate migration implementation. */
#include "../../src/params/db.h"
#include "../../src/params/migrations.h"
#include "../../src/cfg/digital_modes.h"
#include <assert.h>
#include <stdio.h>

sqlite3 *db;
static const char *defaults_path;
static void exec(const char *sql) {
    char *error = NULL;
    int rc = sqlite3_exec(db, sql, NULL, NULL, &error);
    if (rc != SQLITE_OK) fprintf(stderr, "%s: %s\n", sql, error ? error : "SQLite error");
    sqlite3_free(error);
    assert(rc == SQLITE_OK);
}
static int value(const char *sql) {
    sqlite3_stmt *stmt = NULL;
    assert(sqlite3_prepare_v2(db, sql, -1, &stmt, NULL) == SQLITE_OK);
    assert(sqlite3_step(stmt) == SQLITE_ROW);
    int result = sqlite3_column_int(stmt, 0);
    assert(sqlite3_step(stmt) == SQLITE_DONE);
    sqlite3_finalize(stmt);
    return result;
}
static void fixture(int version) {
    sqlite3 *defaults;
    assert(sqlite3_open_v2(defaults_path, &defaults, SQLITE_OPEN_READONLY, NULL) == SQLITE_OK);
    assert(sqlite3_open(":memory:", &db) == SQLITE_OK);
    sqlite3_backup *copy = sqlite3_backup_init(db, "main", defaults, "main");
    assert(copy && sqlite3_backup_step(copy, -1) == SQLITE_DONE);
    assert(sqlite3_backup_finish(copy) == SQLITE_OK);
    assert(sqlite3_close(defaults) == SQLITE_OK);
    char sql[256];
    snprintf(sql, sizeof(sql), "DELETE FROM version; INSERT INTO version VALUES(%d); "
             "DELETE FROM digital_modes WHERE type=6;", version);
    exec(sql);
}
static void finish(void) {
    assert(sqlite3_close(db) == SQLITE_OK);
    db = NULL;
}
static void reopen_fixture(void) {
    /* Close the original connection and reopen the committed database state;
     * no in-process origin value or TEMP trigger can survive this boundary. */
    sqlite3 *next;
    assert(sqlite3_get_autocommit(db));
    assert(sqlite3_open(":memory:", &next) == SQLITE_OK);
    sqlite3_backup *copy = sqlite3_backup_init(next, "main", db, "main");
    assert(copy && sqlite3_backup_step(copy, -1) == SQLITE_DONE);
    assert(sqlite3_backup_finish(copy) == SQLITE_OK);
    assert(sqlite3_close(db) == SQLITE_OK);
    db = next;
}
static void complete(int origin) {
    assert(!migrations_apply());
    assert(value("SELECT id FROM version") == 16);
    char sql[128];
    snprintf(sql, sizeof(sql), "SELECT value FROM freedv_migrations WHERE name='origin_version'");
    assert(value(sql) == origin);
    assert(value("SELECT count(*) FROM freedv_migrations WHERE name IN "
                 "('tx_policy_bridge_v1','bookmarks_v1','app_action_bridge_v1') AND value=1") == 3);
}
static void fresh_and_pre14(void) {
    fixture(1);
    complete(1);
    assert(value("SELECT count(*) FROM digital_modes WHERE type=6") == 17);
    finish();

    fixture(13);
    exec("INSERT INTO digital_modes(label,freq,mode,type) VALUES('My edited bookmark',1870000,2,6);"
         "INSERT INTO params VALUES('tx_outside_bands',1);");
    complete(13);
    assert(value("SELECT count(*) FROM digital_modes WHERE type=6") == 17);
    assert(value("SELECT count(*) FROM digital_modes WHERE type=6 AND freq=1870000 "
                 "AND label='My edited bookmark' AND mode=2") == 1);
    assert(!value("SELECT count(*) FROM params WHERE name='tx_outside_bands'"));
    exec("DELETE FROM digital_modes WHERE type=6; INSERT INTO params VALUES('tx_outside_bands',1);");
    complete(13);
    assert(!value("SELECT count(*) FROM digital_modes WHERE type=6"));
    assert(value("SELECT val FROM params WHERE name='tx_outside_bands'") == 1);
    finish();

    /* A missing version row exercises numeric step zero and the transactional
     * table conversion at step one, not just the later feature bridge. */
    fixture(1);
    exec("DELETE FROM version");
    complete(-1);
    assert(value("SELECT count(*) FROM digital_modes WHERE type=6") == 17);
    finish();
}
static void legacy14(void) {
    for (int empty = 0; empty <= 1; empty++) {
        fixture(14);
        exec("INSERT INTO params VALUES('tx_outside_bands',1), ('freedv_usb_mic_gain',16),"
             "('freedv_timeout',180), ('freedv_usage_hidden',1), ('freedv_usb_headset','USB stable ID'),"
             "('freedv_usb_gains','saved gain profile'), ('freedv_usb_volumes','saved volume profile'),"
             "('long_app',126), ('press_f1',127), ('unrelated',126);");
        if (!empty) exec("INSERT INTO digital_modes(label,freq,mode,type) VALUES"
                         "('Moved',7177100,2,6),('Edited',1870000,2,6)");
        complete(14);
        assert(value("SELECT count(*) FROM digital_modes WHERE type=6") == (empty ? 0 : 2));
        assert(!value("SELECT count(*) FROM digital_modes WHERE type=6 AND freq=7177000"));
        assert(!value("SELECT count(*) FROM params WHERE name='tx_outside_bands'"));
        assert(value("SELECT val FROM params WHERE name='long_app'") == 126);
        assert(value("SELECT val FROM params WHERE name='press_f1'") == 127);
        assert(value("SELECT val FROM params WHERE name='unrelated'") == 126);
        assert(value("SELECT count(*) FROM params WHERE (name='freedv_usb_mic_gain' AND val=16) OR "
                     "(name='freedv_timeout' AND val=180) OR (name='freedv_usage_hidden' AND val=1) OR "
                     "(name='freedv_usb_headset' AND val='USB stable ID') OR "
                     "(name='freedv_usb_gains' AND val='saved gain profile') OR "
                     "(name='freedv_usb_volumes' AND val='saved volume profile')") == 6);
        exec("INSERT INTO params VALUES('tx_outside_bands',1)");
        complete(14);
        assert(value("SELECT val FROM params WHERE name='tx_outside_bands'") == 1);
        finish();
    }
}
static void upstream15_and16(void) {
    for (int version = 15; version <= 16; version++) {
        fixture(version);
        exec("INSERT INTO params VALUES('tx_outside_bands',1),('long_gen',126),('long_app',126),"
             "('long_key',126),('long_msg',126),('long_dfn',126),('long_dfl',126),('long_fst',126),"
             "('press_f1',126),('press_f2',126),('press_f3',126),('long_f1',126),('long_f2',126),"
             "('long_f3',126),('unrelated',126)");
        complete(version);
        assert(!value("SELECT count(*) FROM digital_modes WHERE type=6"));
        assert(value("SELECT count(*) FROM params WHERE val=128") == 13);
        assert(value("SELECT val FROM params WHERE name='unrelated'") == 126);
        assert(value("SELECT val FROM params WHERE name='tx_outside_bands'") == 1);
        exec("UPDATE params SET val=126 WHERE name='long_app'");
        complete(version);
        assert(value("SELECT val FROM params WHERE name='long_app'") == 126);
        finish();
    }
}
static void failed_numeric_retry(void) {
    fixture(14);
    exec("INSERT INTO params VALUES('screen_off_min',3),('tx_outside_bands',1),('long_app',126);"
         "CREATE TEMP TRIGGER fail_screen BEFORE INSERT ON params WHEN NEW.name='screen_saver_min' "
         "BEGIN SELECT RAISE(FAIL,'injected numeric migration failure'); END;");
    assert(migrations_apply());
    assert(sqlite3_get_autocommit(db));
    assert(value("SELECT id FROM version") == 15);
    assert(value("SELECT value FROM freedv_migrations WHERE name='origin_version'") == 14);
    assert(!value("SELECT count(*) FROM freedv_migrations WHERE name='tx_policy_bridge_v1'"));
    reopen_fixture();
    complete(14);
    assert(!value("SELECT count(*) FROM params WHERE name='tx_outside_bands'"));
    assert(value("SELECT val FROM params WHERE name='long_app'") == 126);
    finish();
}
static void failed_bridge_retry(void) {
    fixture(14);
    /* Fail the origin insert first: neither a partial origin
     * is accepted as completed work. */
    exec("CREATE TABLE freedv_migrations(name TEXT PRIMARY KEY,value INTEGER NOT NULL);"
         "CREATE TEMP TRIGGER fail_origin BEFORE INSERT ON freedv_migrations "
         "WHEN NEW.name='origin_version' BEGIN SELECT RAISE(FAIL,'injected origin failure'); END;");
    assert(migrations_apply());
    assert(sqlite3_get_autocommit(db));
    assert(value("SELECT id FROM version") == 14);
    assert(!value("SELECT count(*) FROM freedv_migrations"));
    exec("DROP TRIGGER fail_origin; INSERT INTO params VALUES('tx_outside_bands',1);"
         "CREATE TEMP TRIGGER fail_bridge BEFORE INSERT ON freedv_migrations "
         "WHEN NEW.name='bookmarks_v1' BEGIN SELECT RAISE(FAIL,'injected bridge failure'); END;");
    assert(migrations_apply());
    assert(sqlite3_get_autocommit(db));
    assert(value("SELECT id FROM version") == 16);
    assert(value("SELECT value FROM freedv_migrations WHERE name='origin_version'") == 14);
    assert(value("SELECT count(*) FROM freedv_migrations") == 1);
    assert(value("SELECT val FROM params WHERE name='tx_outside_bands'") == 1);
    reopen_fixture();
    complete(14);
    assert(!value("SELECT count(*) FROM params WHERE name='tx_outside_bands'"));
    finish();
}
static void failed_version_retry(void) {
    fixture(13);
    exec("INSERT INTO params VALUES('tx_outside_bands',1);"
         "CREATE TEMP TRIGGER fail_version BEFORE UPDATE ON version WHEN NEW.id=14 "
         "BEGIN SELECT RAISE(FAIL,'injected version write failure'); END;");
    assert(migrations_apply());
    assert(sqlite3_get_autocommit(db));
    assert(value("SELECT id FROM version") == 13);
    assert(value("SELECT val FROM params WHERE name='tx_outside_bands'") == 1);
    reopen_fixture();
    complete(13);
    assert(!value("SELECT count(*) FROM params WHERE name='tx_outside_bands'"));
    finish();
}
static void completed_bridge_stays_complete(void) {
    fixture(14);
    complete(14);
    /* A current numeric version may retain an older durable origin. Completed
     * feature markers, rather than that old number, determine what runs. */
    exec("INSERT INTO params VALUES('tx_outside_bands',1),('long_app',128),"
         "('press_f1',126),('freedv_usb_mic_gain',16),('freedv_usage_hidden',1);"
         "DELETE FROM digital_modes WHERE type=6;"
         "INSERT INTO digital_modes(label,freq,mode,type) VALUES('Personal',7177100,2,6)");
    reopen_fixture();
    for (int repeat = 0; repeat < 2; ++repeat) {
        complete(14);
        assert(value("SELECT val FROM params WHERE name='tx_outside_bands'") == 1);
        assert(value("SELECT val FROM params WHERE name='long_app'") == 128);
        assert(value("SELECT val FROM params WHERE name='press_f1'") == 126);
        assert(value("SELECT val FROM params WHERE name='freedv_usb_mic_gain'") == 16);
        assert(value("SELECT val FROM params WHERE name='freedv_usage_hidden'") == 1);
        assert(value("SELECT count(*) FROM digital_modes WHERE type=6") == 1);
        assert(value("SELECT mode FROM digital_modes WHERE type=6 AND freq=7177100") == 2);
        reopen_fixture();
    }
    finish();
}
int main(int argc, char **argv) {
    assert(argc == 2 && CFG_DIG_TYPE_FREEDV == 6);
    defaults_path = argv[1];
    fresh_and_pre14();
    legacy14();
    upstream15_and16();
    failed_numeric_retry();
    failed_bridge_retry();
    failed_version_retry();
    completed_bridge_stays_complete();
    puts("PASS: upstream numbering, saved FreeDV preferences/bookmarks, action IDs, durable origin and atomic retry");
    return 0;
}
