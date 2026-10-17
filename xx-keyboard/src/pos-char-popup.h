/*
 * Copyright (C) 2022 Purism SPC
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

#define POS_TYPE_CHAR_POPUP (pos_char_popup_get_type ())

G_DECLARE_FINAL_TYPE (PosCharPopup, pos_char_popup, POS, CHAR_POPUP, GtkPopover)

PosCharPopup *pos_char_popup_new (GtkWidget *relative_to, GStrv symbols);
void pos_char_popup_set_symbols (PosCharPopup *self, GStrv symbols);

G_END_DECLS
