// SPDX-License-Identifier: LGPL-2.1-or-later
#include "../../src/params/db.h"
#include "../../src/params/migrations.h"
#include "../../src/cfg/digital_modes.h"
#include "../../src/cfg/db.h"
#include "../../src/freedv/writer_gate.h"
#include "../../src/freedv/mode.h"
#include <assert.h>
#include <stdio.h>
sqlite3 *db;
bool scheduler_on_gui_thread(void){return true;}
#ifdef ENABLE_FREEDV_VOICE
/* This test exercises the real DB/migrations without constructing an LVGL app. */
bool freedv_mode_active(void){return false;}
bool freedv_mode_request(fd_local_operation operation,int32_t value){(void)operation;(void)value;return false;}
#endif
static int value(const char *sql){sqlite3_stmt *s;assert(sqlite3_prepare_v2(db,sql,-1,&s,NULL)==SQLITE_OK);assert(sqlite3_step(s)==SQLITE_ROW);int n=sqlite3_column_int(s,0);sqlite3_finalize(s);return n;}
static void exec(const char *sql){assert(sqlite3_exec(db,sql,NULL,NULL,NULL)==SQLITE_OK);}
int main(int argc,char **argv){
    assert(argc==2 && CFG_DIG_TYPE_FREEDV==6);
    sqlite3 *defaults;assert(sqlite3_open_v2(argv[1],&defaults,SQLITE_OPEN_READONLY,NULL)==SQLITE_OK);
    assert(sqlite3_open(":memory:",&db)==SQLITE_OK);
    sqlite3_backup *backup=sqlite3_backup_init(db,"main",defaults,"main");assert(backup);
    assert(sqlite3_backup_step(backup,-1)==SQLITE_DONE);assert(sqlite3_backup_finish(backup)==SQLITE_OK);sqlite3_close(defaults);
    assert(value("SELECT count(*) FROM digital_modes WHERE type=6")==17);
    exec("CREATE TABLE IF NOT EXISTS version(id INTEGER); DELETE FROM version; INSERT INTO version VALUES(13); DELETE FROM digital_modes WHERE type=6; INSERT INTO digital_modes(label,freq,mode,type) VALUES('User edit',1870000,2,6)");
    assert(!migrations_apply());assert(value("SELECT count(*) FROM digital_modes WHERE type=6")==17);
    assert(value("SELECT count(*) FROM digital_modes WHERE type=6 AND freq=1870000 AND label='User edit' AND mode=2")==1);
    exec("UPDATE digital_modes SET freq=7177100,label='New bookmark' WHERE type=6 AND freq=7177000; DELETE FROM digital_modes WHERE type=6 AND freq=28720000");
    assert(!migrations_apply());assert(value("SELECT count(*) FROM digital_modes WHERE type=6")==16);
    assert(!value("SELECT count(*) FROM digital_modes WHERE type=6 AND freq IN(7177000,28720000)"));
    assert(value("SELECT count(*) FROM digital_modes WHERE type=6 AND freq=7177100 AND label='New bookmark'")==1);
    assert(cfg_db_init(db));
    assert(cfg_digital_count(CFG_DIG_TYPE_FREEDV)==16);
    assert(cfg_digital_bookmark_save(1870000,1871000,"Edited USB bookmark"));
    assert(value("SELECT mode FROM digital_modes WHERE type=6 AND freq=1871000")==2);
    assert(!cfg_digital_bookmark_save(1871000,7177100,"Duplicate"));
    assert(value("SELECT count(*) FROM digital_modes WHERE type=6 AND freq=1871000")==1);
    assert(fd_writer_voice_reserve());
    assert(!cfg_digital_bookmark_save(0,7100000,"Remote edit"));
    assert(!cfg_digital_bookmark_delete(1871000));
    assert(fd_writer_voice_begin());
    assert(cfg_digital_bookmark_save(0,7100000,"Local bookmark"));
    assert(cfg_digital_bookmark_delete(1871000));
    fd_writer_voice_end();fd_writer_release();
    assert(cfg_digital_count(CFG_DIG_TYPE_FREEDV)==16);
    cfg_db_shutdown();
    sqlite3_close(db);puts("PASS: 17 fresh HF seeds, one-time upgrade preserves USB storage/user edits/deletions and bookmark type 6");
}
