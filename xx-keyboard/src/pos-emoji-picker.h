/*
 * Copyright (C) 2021 Purism SPC
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Author: Guido Günther <agx@sigxcpu.org>
 */

#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

#define POS_TYPE_EMOJI_PICKER         (pos_emoji_picker_get_type ())

G_DECLARE_FINAL_TYPE (PosEmojiPicker, pos_emoji_picker, POS, EMOJI_PICKER, GtkBox)

GtkWidget *pos_emoji_picker_new      (void);

G_END_DECLS
