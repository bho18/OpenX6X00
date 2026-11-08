/*
 *  SPDX-License-Identifier: LGPL-2.1-or-later
 *
 *  Xiegu X6100 LVGL GUI
 *
 *  Copyright (c) 2026 Giovanni Ricciardi IU1WXY
 */

#pragma once

#include "freedv/features.h"
#include "kbd_keymap.h"    /* KEYBOARD_SCRL_LOCK, KEYBOARD_PRINT */
#include "lvgl/lvgl.h"

/* FreeDV popups retain their local keyboard softkeys. The ordinary upstream
 * keyboard still leaves F keys unmapped and uses CapsLock chords. */
#define KEYBOARD_F1 (0xFFBE)
#define KEYBOARD_F2 (0xFFBF)
#define KEYBOARD_F3 (0xFFC0)
#define KEYBOARD_F4 (0xFFC1)
#define KEYBOARD_F5 (0xFFC2)

/* The group of the knobs, the buttons and the keyboard: the focused widget gets their keys */
extern lv_group_t *keyboard_group;

/* Set up the shared LVGL group independently of physical keyboard discovery. */
void keyboard_group_init(void);
void keyboard_init();

/* A keyboard is plugged in and open */
bool keyboard_ready();

#ifdef ENABLE_FREEDV_CONTROL
bool keyboard_diagnostic_reset(void);
#ifdef ENABLE_FREEDV_VOICE
bool keyboard_voice_local_event(lv_indev_t *,unsigned generation);
#endif
#endif

/* What CapsLock and a key held together do, a kbd_hyper_action_t (kbd_hyper.h): run once the key's read is over */
void keyboard_chord(int action);
