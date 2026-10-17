/*
 * Copyright (C) 2021 Purism SPC
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#pragma once

#include "virtual-keyboard-unstable-v1-client-protocol.h"

#include <gdk/gdkwayland.h>
#include <glib-object.h>

G_BEGIN_DECLS

/**
 * PosVirtualKeyboardModifierFlags:
 *
 * Modifiers matching the ones from wl_keyboard.
 */
typedef enum {
  POS_VIRTUAL_KEYBOARD_MODIFIERS_NONE  = 0,
  POS_VIRTUAL_KEYBOARD_MODIFIERS_SHIFT = (1 << 0),
  POS_VIRTUAL_KEYBOARD_MODIFIERS_CTRL  = (1 << 2),
  POS_VIRTUAL_KEYBOARD_MODIFIERS_ALT   = (1 << 3),
  POS_VIRTUAL_KEYBOARD_MODIFIERS_SUPER = (1 << 6),
  POS_VIRTUAL_KEYBOARD_MODIFIERS_ALTGR = (1 << 7),
} PosVirtualKeyboardModifierFlags;

#define POS_TYPE_VIRTUAL_KEYBOARD (pos_virtual_keyboard_get_type ())

G_DECLARE_FINAL_TYPE (PosVirtualKeyboard, pos_virtual_keyboard, POS, VIRTUAL_KEYBOARD, GObject)

PosVirtualKeyboard *pos_virtual_keyboard_new (
  struct zwp_virtual_keyboard_manager_v1 *virtual_keyboard_manager,
  struct wl_seat *_seat);
void pos_virtual_keyboard_press (PosVirtualKeyboard *self, guint keycode);
void pos_virtual_keyboard_release (PosVirtualKeyboard *self, guint keycode);
void pos_virtual_keyboard_set_modifiers (PosVirtualKeyboard             *self,
                                         PosVirtualKeyboardModifierFlags depressed,
                                         PosVirtualKeyboardModifierFlags latched,
                                         PosVirtualKeyboardModifierFlags locked);
void pos_virtual_keyboard_set_keymap (PosVirtualKeyboard *self, const char *keymap);

G_END_DECLS
