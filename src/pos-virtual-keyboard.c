/*
 * Copyright (C) 2022 Purism SPC
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Author: Guido Günther <agx@sigxcpu.org>
 */

#define G_LOG_DOMAIN "pos-virtual-keyboard"

#include "pos-config.h"
#include "util.h"

#include "pos-virtual-keyboard.h"

#include <fcntl.h>
#include <sys/mman.h>


enum {
  PROP_0,
  PROP_VIRTUAL_KEYBOARD_MANAGER,
  PROP_WL_SEAT,
  PROP_LAST_PROP
};
static GParamSpec *props[PROP_LAST_PROP];

/**
 * PosVirtualKeyboard:
 *
 * A Wayland virtual keyboard that gets its keymaps from GNOME.
 * It's not concerned with any rendering.
 */
struct _PosVirtualKeyboard {
  GObject                                 parent;

  struct wl_seat                         *wl_seat;
  struct zwp_virtual_keyboard_manager_v1 *virtual_keyboard_manager;
  struct zwp_virtual_keyboard_v1         *virtual_keyboard;

  GTimer                                 *timer;
};
G_DEFINE_TYPE (PosVirtualKeyboard, pos_virtual_keyboard, G_TYPE_OBJECT)


static void
pos_virtual_keyboard_set_property (GObject      *object,
                                   guint         property_id,
                                   const GValue *value,
                                   GParamSpec   *pspec)
{
  PosVirtualKeyboard *self = POS_VIRTUAL_KEYBOARD (object);

  switch (property_id) {
  case PROP_VIRTUAL_KEYBOARD_MANAGER:
    self->virtual_keyboard_manager = g_value_get_pointer (value);
    break;
  case PROP_WL_SEAT:
    self->wl_seat = g_value_get_pointer (value);
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID (object, property_id, pspec);
    break;
  }
}


static void
pos_virtual_keyboard_constructed (GObject *object)
{
  PosVirtualKeyboard *self = POS_VIRTUAL_KEYBOARD (object);

  G_OBJECT_CLASS (pos_virtual_keyboard_parent_class)->constructed (object);

  self->virtual_keyboard = zwp_virtual_keyboard_manager_v1_create_virtual_keyboard (
    self->virtual_keyboard_manager, self->wl_seat);
}


static void
pos_virtual_keyboard_finalize (GObject *object)
{
  PosVirtualKeyboard *self = POS_VIRTUAL_KEYBOARD (object);

  g_clear_pointer (&self->virtual_keyboard, zwp_virtual_keyboard_v1_destroy);
  g_clear_pointer (&self->timer, g_timer_destroy);

  G_OBJECT_CLASS (pos_virtual_keyboard_parent_class)->finalize (object);
}


static void
pos_virtual_keyboard_class_init (PosVirtualKeyboardClass *klass)
{
  GObjectClass *object_class = G_OBJECT_CLASS (klass);

  object_class->set_property = pos_virtual_keyboard_set_property;
  object_class->constructed = pos_virtual_keyboard_constructed;
  object_class->finalize = pos_virtual_keyboard_finalize;

  props[PROP_VIRTUAL_KEYBOARD_MANAGER] =
    g_param_spec_pointer ("wl-virtual-keyboard-manager",
                          "",
                          "",
                          G_PARAM_WRITABLE | G_PARAM_CONSTRUCT_ONLY);

  props[PROP_WL_SEAT] =
    g_param_spec_pointer ("wl-seat",
                          "",
                          "",
                          G_PARAM_WRITABLE | G_PARAM_CONSTRUCT_ONLY);

  g_object_class_install_properties (object_class, PROP_LAST_PROP, props);
}


static void
pos_virtual_keyboard_init (PosVirtualKeyboard *self)
{
  self->timer = g_timer_new ();
}


PosVirtualKeyboard *
pos_virtual_keyboard_new (struct zwp_virtual_keyboard_manager_v1 *virtual_keyboard_manager,
                          struct wl_seat                         *wl_seat)
{
  GObject *self = g_object_new (POS_TYPE_VIRTUAL_KEYBOARD,
                                "wl-virtual_keyboard_manager", virtual_keyboard_manager,
                                "wl-seat", wl_seat,
                                NULL);

  return POS_VIRTUAL_KEYBOARD (self);
}



void
pos_virtual_keyboard_press (PosVirtualKeyboard *self, guint keycode)
{
  guint millis;

  g_return_if_fail (POS_IS_VIRTUAL_KEYBOARD (self));

  millis = (guint)g_timer_elapsed (self->timer, NULL) * 1000;
  zwp_virtual_keyboard_v1_key (self->virtual_keyboard, millis, keycode,
                               WL_KEYBOARD_KEY_STATE_PRESSED);
}


void
pos_virtual_keyboard_release (PosVirtualKeyboard *self, guint keycode)
{
  guint millis;

  g_return_if_fail (POS_IS_VIRTUAL_KEYBOARD (self));
  millis = (guint)g_timer_elapsed (self->timer, NULL) * 1000;
  zwp_virtual_keyboard_v1_key (self->virtual_keyboard, millis, keycode,
                               WL_KEYBOARD_KEY_STATE_RELEASED);
}


void
pos_virtual_keyboard_set_modifiers (PosVirtualKeyboard             *self,
                                    PosVirtualKeyboardModifierFlags depressed,
                                    PosVirtualKeyboardModifierFlags latched,
                                    PosVirtualKeyboardModifierFlags locked)
{
  g_return_if_fail (POS_IS_VIRTUAL_KEYBOARD (self));

  zwp_virtual_keyboard_v1_modifiers (self->virtual_keyboard,
                                     depressed, locked, latched, 0 /* TBD */);
}

/**
 * pos_virtual_keyboard_set_keymap:
 * @self: The virtual keyboard driver
 * @keymap: The keymap to set
 *
 * Sets the given keymap.
 */
void
pos_virtual_keyboard_set_keymap (PosVirtualKeyboard *self, const char *keymap)
{
  int fd;
  gpointer ptr;
  gsize size;

  g_return_if_fail (POS_IS_VIRTUAL_KEYBOARD (self));
  g_return_if_fail (keymap);

  size = strlen (keymap);
  fd = phosh_create_shm_file (size);
  ptr = mmap (NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
  memmove (ptr, keymap, size);
  zwp_virtual_keyboard_v1_keymap (self->virtual_keyboard,
                                  WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1,
                                  fd, size);
  close (fd);
  g_debug ("Loaded keymap of %zd bytes", size);
}
