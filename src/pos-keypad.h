/*
 * Copyright (C) 2024 The Phosh Developers
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

#define POS_TYPE_KEYPAD (pos_keypad_get_type ())

G_DECLARE_FINAL_TYPE (PosKeypad, pos_keypad, POS, KEYPAD, GtkBin)

GtkWidget *pos_keypad_new (void);
void       pos_keypad_set_letters_visible (PosKeypad *self, gboolean visible);
gboolean   pos_keypad_get_letters_visible (PosKeypad *self);
void       pos_keypad_set_symbols_visible (PosKeypad *self, gboolean visible);
gboolean   pos_keypad_get_symbols_visible (PosKeypad *self);
void       pos_keypad_set_decimal_separator_visible (PosKeypad *self, gboolean visible);
gboolean   pos_keypad_get_decimal_separator_visible (PosKeypad *self);
void       pos_keypad_set_start_action (PosKeypad *self, GtkWidget *start_action);
GtkWidget *pos_keypad_get_start_action (PosKeypad *self);
void       pos_keypad_set_end_action (PosKeypad *self, GtkWidget *end_action);
GtkWidget *pos_keypad_get_end_action (PosKeypad *self);


G_END_DECLS
