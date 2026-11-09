/* SPDX-License-Identifier: LGPL-2.1-or-later */
#pragma once

#include "lvgl/lvgl.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* A native LVGL slider whose integer positions represent complete values.
 * The caller owns styling/placement. Deleting its parent cancels an unfinished
 * preview and destroys the row. Callbacks execute on the GUI thread. */
typedef struct whole_value_row whole_value_row_t;
typedef struct {
    bool (*preview)(int32_t value, void *context);
    bool (*commit)(int32_t value, void *context);
    void (*cancel)(int32_t original, void *context);
    void (*changed)(whole_value_row_t *row, void *context);
    void (*format)(char *text, size_t size, int32_t value, void *context);
    bool (*allowed)(void *context);
    void *context;
} whole_value_row_callbacks_t;

whole_value_row_t *whole_value_row_create(lv_obj_t *parent, lv_group_t *group,
    int32_t value, int32_t minimum, int32_t maximum, int32_t step,
    const whole_value_row_callbacks_t *callbacks);
lv_obj_t *whole_value_row_object(const whole_value_row_t *row);
lv_obj_t *whole_value_row_label(const whole_value_row_t *row);
bool whole_value_row_editing(const whole_value_row_t *row);
int32_t whole_value_row_value(const whole_value_row_t *row);
int32_t whole_value_row_original(const whole_value_row_t *row);
bool whole_value_row_step(whole_value_row_t *row, int32_t detents);
bool whole_value_row_commit(whole_value_row_t *row);
void whole_value_row_cancel(whole_value_row_t *row);
void whole_value_row_destroy(whole_value_row_t *row);

#ifdef __cplusplus
}
#endif
