/* SPDX-License-Identifier: LGPL-2.1-or-later */
#include "whole_value_row.h"
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>

struct whole_value_row {
    lv_obj_t *object, *label;
    lv_group_t *group;
    whole_value_row_callbacks_t callbacks;
    int32_t minimum, maximum, step, value, original;
    uint32_t press_tick;
    bool editing, changing_group, deleting, ignore_release, pointer_entered, press_seen;
};

static bool allowed(const whole_value_row_t *row) {
    return !row->callbacks.allowed || row->callbacks.allowed(row->callbacks.context);
}

static void redraw(whole_value_row_t *row) {
    char text[64];
    if(row->callbacks.format) row->callbacks.format(text,sizeof(text),row->value,row->callbacks.context);
    else snprintf(text,sizeof(text),"%ld",(long)row->value);
    text[sizeof(text)-1]=0;
    lv_label_set_text(row->label,text);
    lv_slider_set_value(row->object,(row->value-row->minimum)/row->step,LV_ANIM_OFF);
    if(row->editing) lv_obj_add_state(row->object,LV_STATE_EDITED);
    else lv_obj_clear_state(row->object,LV_STATE_EDITED);
}

static void changed(whole_value_row_t *row) {
    redraw(row);
    if(row->callbacks.changed) row->callbacks.changed(row,row->callbacks.context);
}

static void group_editing(whole_value_row_t *row,bool editing) {
    if(!row->group || row->changing_group) return;
    row->changing_group=true;
    lv_group_set_editing(row->group,editing);
    row->changing_group=false;
}

static bool begin(whole_value_row_t *row) {
    if(!row || row->deleting || !allowed(row)) return false;
    if(!row->editing) {
        row->original=row->value;
        row->editing=true;
        group_editing(row,true);
        changed(row);
    }
    return true;
}

static bool set_value(whole_value_row_t *row,int32_t value) {
    if(!begin(row)) {redraw(row);return false;}
    if(value==row->value) {redraw(row);return true;}
    if(row->callbacks.preview && !row->callbacks.preview(value,row->callbacks.context)) {
        changed(row);return false;
    }
    row->value=value;
    changed(row);
    return true;
}

bool whole_value_row_step(whole_value_row_t *row,int32_t detents) {
    if(!row || !begin(row)) return false;
    int64_t value=(int64_t)row->value+(int64_t)detents*row->step;
    if(value<row->minimum) value=row->minimum;
    if(value>row->maximum) value=row->maximum;
    return set_value(row,(int32_t)value);
}

bool whole_value_row_commit(whole_value_row_t *row) {
    if(!row || !row->editing || !allowed(row)) return false;
    if(row->callbacks.commit && !row->callbacks.commit(row->value,row->callbacks.context)) {
        group_editing(row,true);changed(row);return false;
    }
    row->original=row->value;
    row->editing=false;
    group_editing(row,false);
    changed(row);
    return true;
}

void whole_value_row_cancel(whole_value_row_t *row) {
    if(!row || !row->editing) return;
    if(row->value!=row->original) {
        if(row->callbacks.cancel) row->callbacks.cancel(row->original,row->callbacks.context);
        else if(row->callbacks.preview) (void)row->callbacks.preview(row->original,row->callbacks.context);
    }
    row->value=row->original;
    row->editing=false;
    group_editing(row,false);
    if(!row->deleting) changed(row);
}

static lv_indev_type_t input_type(void) {
    lv_indev_t *input=lv_indev_get_act();
    return input ? lv_indev_get_type(input) : LV_INDEV_TYPE_NONE;
}

/* Validate an encoder release before the native slider leaves edit mode. The
 * slider retains its own navigation, key/value handling and rendering. */
static void preprocess(lv_event_t *event) {
    whole_value_row_t *row=lv_event_get_user_data(event);
    lv_event_code_t code=lv_event_get_code(event);
    lv_indev_type_t type=input_type();
    if(code==LV_EVENT_FOCUSED && !row->changing_group && row->group &&
       lv_group_get_focused(row->group)==row->object) {
        if(lv_group_get_editing(row->group)) {
            if(!begin(row)) group_editing(row,false);
        } else if(row->editing) {
            /* LVGL toggles an editable object on long press. A long hold must
             * neither save a draft nor evade failed commit validation. */
            row->ignore_release=true;
            group_editing(row,true);
        }
    } else if(code==LV_EVENT_DEFOCUSED) {
        whole_value_row_cancel(row);
    } else if(code==LV_EVENT_PRESSED) {
        row->ignore_release=false;
        row->press_tick=lv_tick_get();row->press_seen=true;
        row->pointer_entered=type==LV_INDEV_TYPE_POINTER && !row->editing;
        if(type==LV_INDEV_TYPE_POINTER && !begin(row)) lv_event_stop_processing(event);
    } else if(code==LV_EVENT_RELEASED && type==LV_INDEV_TYPE_ENCODER && row->editing) {
        lv_indev_t *input=lv_indev_get_act();
        if(row->press_seen && input && lv_tick_elaps(row->press_tick)>input->driver->long_press_time)
            row->ignore_release=true;
        row->press_seen=false;
        if(!row->ignore_release) (void)whole_value_row_commit(row);
        /* Let the native slider clear its press/drag state. If validation
         * failed, FOCUSED above rejects its subsequent edit-mode exit. */
    } else if(code==LV_EVENT_KEY) {
        uint32_t key=*(uint32_t *)lv_event_get_param(event);
        if(key==LV_KEY_ESC && row->editing) {
            whole_value_row_cancel(row);lv_event_stop_processing(event);
        } else if(key==LV_KEY_ENTER && type!=LV_INDEV_TYPE_ENCODER) {
            /* A keypad sends ENTER on the press. Apply only on its short
             * release, so holding Enter cannot save a draft. */
            lv_event_stop_processing(event);
        } else if((key==LV_KEY_LEFT || key==LV_KEY_RIGHT || key==LV_KEY_UP || key==LV_KEY_DOWN) && !allowed(row)) {
            lv_event_stop_processing(event);
        }
    }
}

static void event_cb(lv_event_t *event) {
    whole_value_row_t *row=lv_event_get_user_data(event);
    switch(lv_event_get_code(event)) {
        case LV_EVENT_VALUE_CHANGED: {
            int32_t position=lv_slider_get_value(row->object);
            (void)set_value(row,row->minimum+position*row->step);
            break;
        }
        case LV_EVENT_SHORT_CLICKED:
            if(input_type()==LV_INDEV_TYPE_POINTER || input_type()==LV_INDEV_TYPE_KEYPAD) {
                if(row->pointer_entered) row->pointer_entered=false;
                else if(row->editing) (void)whole_value_row_commit(row);
                else (void)begin(row);
            }
            break;
        case LV_EVENT_CLICKED:
            /* Also support an explicit programmatic activation without
             * interpreting the encoder's follow-up CLICKED as a second save. */
            if(input_type()==LV_INDEV_TYPE_NONE) {
                if(row->editing) (void)whole_value_row_commit(row);else (void)begin(row);
            }
            break;
        case LV_EVENT_DELETE:
            row->deleting=true;
            whole_value_row_cancel(row);
            free(row);
            break;
        default:break;
    }
}

whole_value_row_t *whole_value_row_create(lv_obj_t *parent,lv_group_t *group,
    int32_t value,int32_t minimum,int32_t maximum,int32_t step,
    const whole_value_row_callbacks_t *callbacks) {
    int64_t span=(int64_t)maximum-minimum;
    if(!parent || !group || step<=0 || span<0 || span>INT32_MAX || span%step ||
       value<minimum || value>maximum || ((int64_t)value-minimum)%step) return NULL;
    whole_value_row_t *row=calloc(1,sizeof(*row));
    if(!row) return NULL;
    row->group=group;row->minimum=minimum;row->maximum=maximum;row->step=step;
    row->original=row->value=value;
    if(callbacks) row->callbacks=*callbacks;
    row->object=lv_slider_create(parent);
    row->label=lv_label_create(row->object);
    lv_obj_center(row->label);
    lv_obj_set_size(row->object,220,44);
    lv_slider_set_mode(row->object,LV_SLIDER_MODE_NORMAL);
    lv_slider_set_range(row->object,0,(int32_t)(span/step));
    lv_obj_clear_flag(row->object,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(row->object,preprocess,LV_EVENT_ALL|LV_EVENT_PREPROCESS,row);
    lv_obj_add_event_cb(row->object,event_cb,LV_EVENT_ALL,row);
    redraw(row);
    lv_group_add_obj(group,row->object);
    return row;
}

lv_obj_t *whole_value_row_object(const whole_value_row_t *row) {return row?row->object:NULL;}
lv_obj_t *whole_value_row_label(const whole_value_row_t *row) {return row?row->label:NULL;}
bool whole_value_row_editing(const whole_value_row_t *row) {return row && row->editing;}
int32_t whole_value_row_value(const whole_value_row_t *row) {return row?row->value:0;}
int32_t whole_value_row_original(const whole_value_row_t *row) {return row?row->original:0;}
void whole_value_row_destroy(whole_value_row_t *row) {if(row) lv_obj_del(row->object);}
