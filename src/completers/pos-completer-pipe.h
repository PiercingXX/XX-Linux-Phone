/*
 * Copyright (C) 2023 The Phosh Developers
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#pragma once

#include "pos-completer.h"

#include <glib-object.h>

G_BEGIN_DECLS

#define POS_TYPE_COMPLETER_PIPE (pos_completer_pipe_get_type ())

G_DECLARE_FINAL_TYPE (PosCompleterPipe, pos_completer_pipe, POS, COMPLETER_PIPE, GObject)

PosCompleter *pos_completer_pipe_new (GError **error);

G_END_DECLS
