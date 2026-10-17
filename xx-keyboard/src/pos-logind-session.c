/*
 * Copyright (C) 2024 Phosh Developers
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Author: Guido Günther <agx@sigxcpu.org>
 */

#define G_LOG_DOMAIN "pos-logind-session"

#include "pos-config.h"

#include "pos-logind-session.h"

#include <gio/gio.h>
#include <systemd/sd-login.h>

/**
 * PosLogindSession:
 *
 * Singleton to track logind session properties
 */

#define LOGIND_BUS_NAME "org.freedesktop.login1"
#define LOGIND_OBJECT_PATH "/org/freedesktop/login1"
#define LOGIND_INTERFACE "org.freedesktop.login1.Manager"
#define LOGIND_SESSION_INTERFACE "org.freedesktop.login1.Session"

enum {
  PROP_0,
  PROP_LOCKED,
  PROP_LAST_PROP
};
static GParamSpec *props[PROP_LAST_PROP];

struct _PosLogindSession {
  GObject       parent;

  gboolean      locked;
  GCancellable *cancellable;

  GDBusProxy   *logind_proxy;
  GDBusProxy   *logind_session_proxy;
};
G_DEFINE_TYPE (PosLogindSession, pos_logind_session, G_TYPE_OBJECT)

static PosLogindSession *manager_object;


static void
set_locked (PosLogindSession *self, gboolean locked)
{
  if (self->locked == locked)
    return;

  self->locked = locked;
  g_object_notify_by_pspec (G_OBJECT (self), props[PROP_LOCKED]);
}


static void
pos_logind_session_get_property (GObject    *object,
                                 guint       property_id,
                                 GValue     *value,
                                 GParamSpec *pspec)
{
  PosLogindSession *self = POS_LOGIND_SESSION (object);

  switch (property_id) {
  case PROP_LOCKED:
    g_value_set_boolean (value, self->locked);
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID (object, property_id, pspec);
    break;
  }
}


static void
pos_logind_session_dispose (GObject *object)
{
  PosLogindSession *self = POS_LOGIND_SESSION (object);

  g_cancellable_cancel (self->cancellable);
  g_clear_object (&self->cancellable);

  g_clear_object (&self->logind_proxy);
  g_clear_object (&self->logind_session_proxy);

  G_OBJECT_CLASS (pos_logind_session_parent_class)->dispose (object);
}


static void
pos_logind_session_class_init (PosLogindSessionClass *klass)
{
  GObjectClass *object_class = G_OBJECT_CLASS (klass);

  object_class->get_property = pos_logind_session_get_property;
  object_class->dispose = pos_logind_session_dispose;

  props[PROP_LOCKED] =
    g_param_spec_boolean ("locked", "", "",
                          FALSE,
                          G_PARAM_READABLE | G_PARAM_EXPLICIT_NOTIFY | G_PARAM_STATIC_STRINGS);

  g_object_class_install_properties (object_class, PROP_LAST_PROP, props);
}


static gboolean
pos_find_systemd_session (char **session_id)
{
  g_autofree char *session = NULL;
  int r;

  g_return_val_if_fail (session_id != NULL && *session_id == NULL, FALSE);

  r = sd_pid_get_session (getpid (), &session);
  if (r == 0) {
    *session_id = g_steal_pointer (&session);
    return TRUE;
  }

  /* Not in a system session, so let logind make a pick */
  r = sd_uid_get_display (getuid (), &session);
  if (r == 0) {
    *session_id = g_steal_pointer (&session);
    return TRUE;
  }

  return FALSE;
}


static void
on_logind_session_signal (PosLogindSession *self, GVariant *changed_props)
{
  gboolean locked;

  g_assert (POS_IS_LOGIND_SESSION (self));

  if (!g_variant_lookup (changed_props, "LockedHint", "b", &locked))
    return;

  g_debug ("locked: %d", locked);
  set_locked (self, locked);
}


static void
on_logind_get_session_proxy_done  (GObject          *object,
                                   GAsyncResult     *res,
                                   PosLogindSession *self)
{
  g_autoptr (GError) err = NULL;
  g_autoptr (GVariant) variant = NULL;

  self->logind_session_proxy = g_dbus_proxy_new_for_bus_finish (res, &err);
  if (!self->logind_session_proxy) {
    g_warning ("Failed to get login1 session proxy: %s", err->message);
    return;
  }

  g_return_if_fail (POS_IS_LOGIND_SESSION (self));

  g_debug ("Got session proxy");
  g_signal_connect_swapped (self->logind_session_proxy, "g-properties-changed",
                            G_CALLBACK (on_logind_session_signal),
                            self);
  variant = g_dbus_proxy_get_cached_property (self->logind_session_proxy, "LockedHint");
  if (!variant) {
    g_warning ("Failed to get locked hint");
    return;
  }

  set_locked (self, g_variant_get_boolean (variant));
}


static void
on_logind_get_session_done (GDBusProxy       *object,
                            GAsyncResult     *res,
                            PosLogindSession *self)
{
  const char *object_path;
  g_autoptr (GError) err = NULL;
  g_autoptr (GVariant) ret = NULL;

  ret = g_dbus_proxy_call_finish (object, res, &err);
  if (!ret) {
    g_warning ("Failed to get session: %s", err->message);
    return;
  }

  g_assert (POS_IS_LOGIND_SESSION (self));

  g_variant_get (ret, "(&o)", &object_path);

  g_debug ("Session at path: %s", object_path);
  /* Register a proxy for this session */
  g_dbus_proxy_new_for_bus (G_BUS_TYPE_SYSTEM,
                            G_DBUS_PROXY_FLAGS_NONE,
                            NULL,
                            LOGIND_BUS_NAME,
                            object_path,
                            LOGIND_SESSION_INTERFACE,
                            self->cancellable,
                            (GAsyncReadyCallback)on_logind_get_session_proxy_done,
                            self);
}


static void
on_logind_proxy_new_for_bus_done (GObject          *source_object,
                                  GAsyncResult     *res,
                                  PosLogindSession *self)
{
  g_autoptr (GError) err = NULL;
  g_autofree char *session_id = NULL;

  self->logind_proxy = g_dbus_proxy_new_for_bus_finish (res, &err);
  if (!self->logind_proxy) {
    g_warning ("Failed to get logind manager proxy: %s", err->message);
    return;
  }

  g_assert (POS_IS_LOGIND_SESSION (self));

  /* If we find a session get it object path */
  if (pos_find_systemd_session (&session_id)) {
    g_debug ("Logind session %s", session_id);

    g_dbus_proxy_call (self->logind_proxy,
                       "GetSession",
                       g_variant_new ("(s)", session_id),
                       G_DBUS_CALL_FLAGS_NONE,
                       -1,
                       self->cancellable,
                       (GAsyncReadyCallback)on_logind_get_session_done,
                       self);
  } else {
    g_warning ("No Login session, screen lock will be unreliable");
  }
}


static void
pos_logind_session_init (PosLogindSession *self)
{
  self->cancellable = g_cancellable_new ();

  /* Connect to logind's session manager */
  g_dbus_proxy_new_for_bus (G_BUS_TYPE_SYSTEM,
                            G_DBUS_PROXY_FLAGS_NONE,
                            NULL,
                            LOGIND_BUS_NAME,
                            LOGIND_OBJECT_PATH,
                            LOGIND_INTERFACE,
                            self->cancellable,
                            (GAsyncReadyCallback) on_logind_proxy_new_for_bus_done,
                            self);
}


PosLogindSession *
pos_logind_session_new (void)
{
  if (manager_object != NULL)
    g_object_ref (manager_object);
  else {
    manager_object = g_object_new (POS_TYPE_LOGIND_SESSION, NULL);
    g_object_add_weak_pointer (G_OBJECT (manager_object), (gpointer *)&manager_object);
  }

  return POS_LOGIND_SESSION (manager_object);
}
