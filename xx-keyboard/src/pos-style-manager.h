/*
 * Copyright (C) 2024 The Phosh Developers
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

#define POS_TYPE_STYLE_MANAGER (pos_style_manager_get_type ())

G_DECLARE_FINAL_TYPE (PosStyleManager, pos_style_manager, POS, STYLE_MANAGER, GObject)

PosStyleManager   *pos_style_manager_new (void);
const char        *pos_style_manager_get_theme_name (PosStyleManager *self);
gboolean           pos_style_manager_is_high_contrast (PosStyleManager *self);

const char        *pos_style_manager_get_stylesheet (const char *theme_name);

G_END_DECLS
