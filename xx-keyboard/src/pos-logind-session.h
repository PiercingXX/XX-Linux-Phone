/*
 * Copyright (C) 2024 Phosh Developers
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#pragma once

#include <glib-object.h>

G_BEGIN_DECLS

#define POS_TYPE_LOGIND_SESSION (pos_logind_session_get_type ())

G_DECLARE_FINAL_TYPE (PosLogindSession, pos_logind_session, POS, LOGIND_SESSION, GObject)

PosLogindSession *pos_logind_session_new (void);

G_END_DECLS
