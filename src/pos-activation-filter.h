/*
 * Copyright (C) 2023 The Phosh Developers
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#pragma once

#include <gtk/gtk.h>

#define POS_TYPE_ACTIVATION_FILTER (pos_activation_filter_get_type())

G_DECLARE_FINAL_TYPE (PosActivationFilter, pos_activation_filter, POS, ACTIVATION_FILTER, GObject)

PosActivationFilter *pos_activation_filter_new                    (gpointer foreign_toplevel_management);
gboolean             pos_activation_filter_allow_active           (PosActivationFilter *self);
