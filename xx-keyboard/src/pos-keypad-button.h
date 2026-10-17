/*
 * Copyright (C) 2024 The Phosh Developers
 *
 * SPDX-License-Identifier: GPL-3-or-later
 *
 * based CuiKeypad which is
 * Copyright (C) 2021 Purism SPC
 */

#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

#define POS_TYPE_KEYPAD_BUTTON (pos_keypad_button_get_type())

G_DECLARE_FINAL_TYPE (PosKeypadButton, pos_keypad_button, POS, KEYPAD_BUTTON, GtkButton)

struct _PosKeypadButtonClass
{
  GtkButtonClass parent_class;
};

GtkWidget   *pos_keypad_button_new                   (const gchar     *symbols);
gchar        pos_keypad_button_get_digit             (PosKeypadButton *self);
const gchar *pos_keypad_button_get_symbols           (PosKeypadButton *self);
void         pos_keypad_button_show_symbols          (PosKeypadButton *self,
                                                      gboolean         visible);

G_END_DECLS
