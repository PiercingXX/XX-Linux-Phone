/*
 * Copyright (C) 2023 The Phosh Developers
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#pragma once

#include "pos-completer.h"

G_BEGIN_DECLS

/* Used by completion implementations */
gboolean       pos_completer_add_preedit (PosCompleter *self,
                                          GString *preedit,
                                          const char *symbol);
gboolean       pos_completer_symbol_is_word_separator (const char *symbol,
                                                       gboolean *is_ws);
gboolean       pos_completer_grab_last_word (const char *before, char **new_before, char **word);
G_END_DECLS
