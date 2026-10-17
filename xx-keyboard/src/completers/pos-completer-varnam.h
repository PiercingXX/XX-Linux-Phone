/*
 * Copyright (C) 2022 Purism SPC
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#pragma once

#include "pos-completer.h"

#include <glib-object.h>

G_BEGIN_DECLS

#define POS_TYPE_COMPLETER_VARNAM (pos_completer_varnam_get_type ())

G_DECLARE_FINAL_TYPE (PosCompleterVarnam, pos_completer_varnam, POS, COMPLETER_VARNAM, GObject)

PosCompleter *pos_completer_varnam_new (GError **error);

G_END_DECLS
