/*
 * Copyright (C) 2024 The Phosh Developers
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#pragma once

#include "phoc-device-state-unstable-v1-client-protocol.h"

#include <glib-object.h>

G_BEGIN_DECLS

#define POS_TYPE_HW_TRACKER (pos_hw_tracker_get_type ())

G_DECLARE_FINAL_TYPE (PosHwTracker, pos_hw_tracker, POS, HW_TRACKER, GObject)

PosHwTracker *pos_hw_tracker_new (struct zphoc_device_state_v1 *device_state);
gboolean      pos_hw_tracker_get_allow_active (PosHwTracker *self);

G_END_DECLS
