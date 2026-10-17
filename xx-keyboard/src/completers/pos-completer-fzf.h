/*
 * Copyright (C) 2022 Purism SPC
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#pragma once

#include "pos-completer.h"

#include <glib-object.h>

G_BEGIN_DECLS

#define POS_TYPE_COMPLETER_FZF (pos_completer_fzf_get_type ())

G_DECLARE_FINAL_TYPE (PosCompleterFzf, pos_completer_fzf, POS, COMPLETER_FZF, GObject)

PosCompleter *pos_completer_fzf_new (GError **error);

G_END_DECLS
