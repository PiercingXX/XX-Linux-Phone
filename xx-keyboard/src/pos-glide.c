/*
 * Copyright (C) 2026 Phosh.mobi e.V.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#define G_LOG_DOMAIN "pos-glide"

#include "pos-config.h"

#include "pos-glide.h"

#include <math.h>
#include <string.h>

#ifdef POS_HAVE_GLIDE
#include "xxglide.h"
#endif

/* A boxed point list. Plain struct (not GObject) so gestures can pass
 * it through signals without a display.
 */
struct _PosGlideStroke {
  GArray *xs;
  GArray *ys;
  GArray *times;
};

G_DEFINE_BOXED_TYPE (PosGlideStroke, pos_glide_stroke,
                     pos_glide_stroke_copy, pos_glide_stroke_free)


PosGlideStroke *
pos_glide_stroke_new (void)
{
  PosGlideStroke *self = g_new0 (PosGlideStroke, 1);

  self->xs = g_array_new (FALSE, FALSE, sizeof (float));
  self->ys = g_array_new (FALSE, FALSE, sizeof (float));
  self->times = g_array_new (FALSE, FALSE, sizeof (int));

  return self;
}


PosGlideStroke *
pos_glide_stroke_copy (PosGlideStroke *self)
{
  PosGlideStroke *copy;

  g_return_val_if_fail (self != NULL, NULL);

  copy = pos_glide_stroke_new ();
  g_array_append_vals (copy->xs, self->xs->data, self->xs->len);
  g_array_append_vals (copy->ys, self->ys->data, self->ys->len);
  g_array_append_vals (copy->times, self->times->data, self->times->len);

  return copy;
}


void
pos_glide_stroke_free (PosGlideStroke *self)
{
  if (self == NULL)
    return;

  g_array_unref (self->xs);
  g_array_unref (self->ys);
  g_array_unref (self->times);
  g_free (self);
}


void
pos_glide_stroke_append (PosGlideStroke *self, float x, float y, int time_ms)
{
  g_return_if_fail (self != NULL);

  g_array_append_val (self->xs, x);
  g_array_append_val (self->ys, y);
  g_array_append_val (self->times, time_ms);
}


int
pos_glide_stroke_get_n_points (PosGlideStroke *self)
{
  g_return_val_if_fail (self != NULL, 0);

  return self->xs->len;
}


void
pos_glide_stroke_get_point (PosGlideStroke *self, int idx, float *x, float *y, int *time_ms)
{
  g_return_if_fail (self != NULL);
  g_return_if_fail (idx >= 0 && idx < (int)self->xs->len);

  if (x)
    *x = g_array_index (self->xs, float, idx);
  if (y)
    *y = g_array_index (self->ys, float, idx);
  if (time_ms)
    *time_ms = g_array_index (self->times, int, idx);
}


gboolean
pos_glide_stroke_exceeds_threshold (PosGlideStroke *self, double key_width)
{
  float x0, y0, x1, y1;
  double dist;
  int n;

  g_return_val_if_fail (self != NULL, FALSE);
  g_return_val_if_fail (key_width > 0, FALSE);

  n = pos_glide_stroke_get_n_points (self);
  if (n < 2)
    return FALSE;

  pos_glide_stroke_get_point (self, 0, &x0, &y0, NULL);
  pos_glide_stroke_get_point (self, n - 1, &x1, &y1, NULL);
  dist = hypot (x1 - x0, y1 - y0);

  return dist >= POS_GLIDE_START_DISTANCE_FRAC * key_width;
}


gunichar
pos_glide_symbol_codepoint (const char *symbol)
{
  gunichar ch;

  if (symbol == NULL)
    return 0;

  ch = g_utf8_get_char_validated (symbol, -1);
  /* Single codepoint only: g_utf8_get_char_validated returns -1/-2
   * for invalid/partial input, and anything followed by more text is
   * not a plain letter key.
   */
  if (ch == (gunichar)-1 || ch == (gunichar)-2)
    return 0;
  if (g_utf8_next_char (symbol) - symbol != (long) strlen (symbol))
    return 0;
  if (!g_unichar_isalpha (ch))
    return 0;

  return g_unichar_tolower (ch);
}


GArray *
pos_glide_build_keys (const char **symbols, const GdkRectangle *boxes, int n, double pitch)
{
  GArray *keys;

  g_return_val_if_fail (symbols != NULL || n == 0, NULL);
  g_return_val_if_fail (boxes != NULL || n == 0, NULL);
  g_return_val_if_fail (n >= 0, NULL);

  keys = g_array_new (FALSE, FALSE, sizeof (PosGlideKey));

  for (int i = 0; i < n; i++) {
    PosGlideKey key;
    gunichar codepoint = pos_glide_symbol_codepoint (symbols[i]);

    if (codepoint == 0)
      continue;

    key.codepoint = codepoint;
    key.x = boxes[i].x;
    key.y = boxes[i].y;
    key.width = boxes[i].width;
    key.height = boxes[i].height;
    g_array_append_val (keys, key);
  }

  /* The pitch is informational here; the caller passes it on as
   * most_common_key_width.
   */
  (void) pitch;

  return keys;
}


static const char *
skip_trailing_space (const char *before, const char **start)
{
  const char *end = before + strlen (before);

  while (end > before) {
    const char *prev = g_utf8_prev_char (end);
    gunichar ch = g_utf8_get_char (prev);

    if (ch != ' ' && ch != '\t')
      break;
    end = prev;
  }

  *start = before;
  return end;
}


gboolean
pos_glide_needs_leading_space (const char *before)
{
  const char *end;
  gunichar ch;

  if (before == NULL || *before == '\0')
    return FALSE;

  end = before + strlen (before);
  ch = g_utf8_get_char (g_utf8_prev_char (end));

  return ch != ' ' && ch != '\n';
}


gboolean
pos_glide_is_sentence_start (const char *before)
{
  const char *start, *end, *prev;
  gunichar ch;

  if (before == NULL || *before == '\0')
    return TRUE;

  end = skip_trailing_space (before, &start);
  if (end == start)
    return TRUE;

  prev = g_utf8_prev_char (end);
  ch = g_utf8_get_char (prev);

  return ch == '.' || ch == '!' || ch == '?' || ch == '\n';
}


char *
pos_glide_prev_word (const char *before)
{
  const char *end, *p;
  g_autoptr (GString) word = NULL;

  if (before == NULL || *before == '\0')
    return NULL;

  end = before + strlen (before);

  /* Skip trailing non-letters (space, punctuation, …) */
  p = end;
  while (p > before) {
    const char *prev = g_utf8_prev_char (p);
    gunichar ch = g_utf8_get_char (prev);

    if (g_unichar_isalpha (ch) || ch == '\'')
      break;
    p = prev;
  }
  end = p;

  /* Take the trailing letter run */
  while (p > before) {
    const char *prev = g_utf8_prev_char (p);
    gunichar ch = g_utf8_get_char (prev);

    if (!g_unichar_isalpha (ch) && ch != '\'')
      break;
    p = prev;
  }

  if (p == end)
    return NULL;

  word = g_string_new_len (p, end - p);

  return g_utf8_strdown (word->str, -1);
}


char *
pos_glide_apply_capitalization (const char *word, gboolean capitalize)
{
  const char *rest;
  GString *out;

  g_return_val_if_fail (word != NULL, NULL);

  if (!capitalize)
    return g_strdup (word);

  out = g_string_new (NULL);
  g_string_append_unichar (out, g_unichar_toupper (g_utf8_get_char (word)));
  rest = g_utf8_next_char (word);
  g_string_append (out, rest);

  return g_string_free (out, FALSE);
}


gsize
pos_glide_pending_backspace_span (const char *glide_word)
{
  g_return_val_if_fail (glide_word != NULL, 0);

  return strlen (glide_word);
}


void
pos_glide_result_free (PosGlideResult *result)
{
  if (result == NULL)
    return;

  g_strfreev (result->words);
  g_free (result);
}

G_DEFINE_AUTOPTR_CLEANUP_FUNC (PosGlideResult, pos_glide_result_free)


/* The decode worker. One XxGlide instance, created lazily on the first
 * decode so dictionary parsing never blocks the UI, and recreated when
 * the dictionary language changes. Everything touching the instance
 * runs under @lock; GTask may use different pool threads over time but
 * never concurrently.
 */
struct _PosGlideDecoder {
  GObject       parent;

  char         *dict_dir;
  char         *dict_path;
  char         *loaded_path;
  gboolean      available;

  GMutex        lock;
#ifdef POS_HAVE_GLIDE
  XxGlide      *glide;
#endif
  GArray       *keys;
  int           width, height, pitch;
  gboolean      layout_dirty;
};

G_DEFINE_TYPE (PosGlideDecoder, pos_glide_decoder, G_TYPE_OBJECT)

enum {
  PROP_0,
  PROP_DICT_DIR,
  PROP_LAST_PROP,
};
static GParamSpec *props[PROP_LAST_PROP];


typedef struct {
  PosGlideStroke *stroke;
  char           *prev_word;
} GlideRequest;


static void
glide_request_free (GlideRequest *req)
{
  pos_glide_stroke_free (req->stroke);
  g_free (req->prev_word);
  g_free (req);
}


static char *
glide_dict_path_for (const char *dict_dir, const char *lang, const char *region)
{
  const char *dict = "main_en-US.dict";

  /* Only en has shipped dictionaries; en-GB when the layout says so */
  if (g_strcmp0 (lang, "en") == 0 && g_strcmp0 (region, "gb") == 0)
    dict = "main_en-GB.dict";

  if (dict_dir == NULL)
    return NULL;

  return g_build_filename (dict_dir, dict, NULL);
}


static void
pos_glide_decoder_set_property (GObject *object, guint property_id,
                                const GValue *value, GParamSpec *pspec)
{
  PosGlideDecoder *self = POS_GLIDE_DECODER (object);

  switch (property_id) {
  case PROP_DICT_DIR:
    g_free (self->dict_dir);
    self->dict_dir = g_value_dup_string (value);
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID (object, property_id, pspec);
    break;
  }
}


static void
pos_glide_decoder_get_property (GObject *object, guint property_id,
                                GValue *value, GParamSpec *pspec)
{
  PosGlideDecoder *self = POS_GLIDE_DECODER (object);

  switch (property_id) {
  case PROP_DICT_DIR:
    g_value_set_string (value, self->dict_dir);
    break;
  default:
    G_OBJECT_WARN_INVALID_PROPERTY_ID (object, property_id, pspec);
    break;
  }
}


static void
pos_glide_decoder_finalize (GObject *object)
{
  PosGlideDecoder *self = POS_GLIDE_DECODER (object);

  g_mutex_lock (&self->lock);
#ifdef POS_HAVE_GLIDE
  xxglide_free (self->glide);
  self->glide = NULL;
#endif
  g_clear_pointer (&self->keys, g_array_unref);
  g_mutex_unlock (&self->lock);
  g_mutex_clear (&self->lock);

  g_free (self->dict_dir);
  g_free (self->dict_path);
  g_free (self->loaded_path);

  G_OBJECT_CLASS (pos_glide_decoder_parent_class)->finalize (object);
}


static void
pos_glide_decoder_class_init (PosGlideDecoderClass *klass)
{
  GObjectClass *object_class = G_OBJECT_CLASS (klass);

  object_class->set_property = pos_glide_decoder_set_property;
  object_class->get_property = pos_glide_decoder_get_property;
  object_class->finalize = pos_glide_decoder_finalize;

  props[PROP_DICT_DIR] =
    g_param_spec_string ("dict-dir", "", "",
                         NULL,
                         G_PARAM_READWRITE | G_PARAM_CONSTRUCT_ONLY |
                         G_PARAM_STATIC_STRINGS);

  g_object_class_install_properties (object_class, PROP_LAST_PROP, props);
}


static void
pos_glide_decoder_init (PosGlideDecoder *self)
{
  g_mutex_init (&self->lock);
}


PosGlideDecoder *
pos_glide_decoder_new (const char *dict_dir)
{
  return POS_GLIDE_DECODER (g_object_new (POS_TYPE_GLIDE_DECODER,
                                          "dict-dir", dict_dir,
                                          NULL));
}


void
pos_glide_decoder_set_language (PosGlideDecoder *self, const char *lang, const char *region)
{
  g_autofree char *path = NULL;

  g_return_if_fail (POS_IS_GLIDE_DECODER (self));

  path = glide_dict_path_for (self->dict_dir, lang, region);

  g_mutex_lock (&self->lock);
  if (g_strcmp0 (self->dict_path, path) != 0) {
    g_free (self->dict_path);
    self->dict_path = g_steal_pointer (&path);
    /* Recreate the instance on the worker; stat here so callers can
     * disable the gesture path right away when no dictionary exists.
     */
    self->layout_dirty = TRUE;
  }
  self->available = self->dict_path != NULL && g_file_test (self->dict_path, G_FILE_TEST_IS_REGULAR);
  g_mutex_unlock (&self->lock);
}


gboolean
pos_glide_decoder_get_available (PosGlideDecoder *self)
{
  gboolean available;

  g_return_val_if_fail (POS_IS_GLIDE_DECODER (self), FALSE);

  g_mutex_lock (&self->lock);
  available = self->available;
  g_mutex_unlock (&self->lock);

  return available;
}


gboolean
pos_glide_decoder_set_layout (PosGlideDecoder *self, const PosGlideKey *keys,
                              int n_keys, int width, int height, int pitch)
{
  g_return_val_if_fail (POS_IS_GLIDE_DECODER (self), FALSE);
  g_return_val_if_fail (keys != NULL || n_keys == 0, FALSE);
  g_return_val_if_fail (n_keys > 0 && width > 0 && height > 0 && pitch > 0, FALSE);

  g_mutex_lock (&self->lock);
  g_clear_pointer (&self->keys, g_array_unref);
  self->keys = g_array_sized_new (FALSE, FALSE, sizeof (PosGlideKey), n_keys);
  g_array_append_vals (self->keys, keys, n_keys);
  self->width = width;
  self->height = height;
  self->pitch = pitch;
  self->layout_dirty = TRUE;
  g_mutex_unlock (&self->lock);

  return TRUE;
}


#ifdef POS_HAVE_GLIDE
/* Must hold @lock. The instance is created for the current dictionary
 * path; a language switch replaces the path (see set_language) and the
 * next decode recreates the instance.
 */
static gboolean
pos_glide_decoder_ensure_locked (PosGlideDecoder *self)
{
  if (g_strcmp0 (self->loaded_path, self->dict_path) != 0) {
    xxglide_free (self->glide);
    self->glide = NULL;
    g_free (self->loaded_path);
    self->loaded_path = NULL;
  }

  if (self->glide == NULL) {
    if (self->dict_path == NULL)
      return FALSE;
    self->glide = xxglide_new (self->dict_path);
    if (self->glide == NULL) {
      /* Expected when no dictionary is installed: glide stays disabled */
      g_debug ("Failed to load glide dictionary %s", self->dict_path);
      return FALSE;
    }
    self->loaded_path = g_strdup (self->dict_path);
    self->layout_dirty = TRUE;
  }

  if (self->layout_dirty) {
    XxGlideKey *keys;
    int n_keys, ok;

    if (self->keys == NULL || self->keys->len == 0)
      return FALSE;

    n_keys = self->keys->len;
    keys = g_new (XxGlideKey, n_keys);
    for (int i = 0; i < n_keys; i++) {
      PosGlideKey *k = &g_array_index (self->keys, PosGlideKey, i);

      keys[i].codepoint = k->codepoint;
      keys[i].x = k->x;
      keys[i].y = k->y;
      keys[i].width = k->width;
      keys[i].height = k->height;
    }
    ok = xxglide_set_layout (self->glide, keys, n_keys,
                             self->width, self->height, self->pitch);
    g_free (keys);
    if (!ok) {
      g_warning ("Failed to set glide layout");
      return FALSE;
    }
    self->layout_dirty = FALSE;
  }

  return TRUE;
}
#endif


typedef struct {
  PosGlideDecodeCallback callback;
  gpointer               user_data;
} DecodeNotify;


static void
decode_thread (GTask *task, gpointer source, gpointer task_data, GCancellable *cancellable)
{
  GlideRequest *req = task_data;
  PosGlideDecoder *self = POS_GLIDE_DECODER (source);
  PosGlideResult *result = g_new0 (PosGlideResult, 1);

#ifdef POS_HAVE_GLIDE
  g_mutex_lock (&self->lock);
  if (!g_cancellable_is_cancelled (cancellable) && pos_glide_decoder_ensure_locked (self)) {
    int n = pos_glide_stroke_get_n_points (req->stroke);
    g_autofree float *xs = g_new (float, MAX (n, 1));
    g_autofree float *ys = g_new (float, MAX (n, 1));
    g_autofree int *times = g_new (int, MAX (n, 1));
    char **words = NULL;
    int n_words;

    for (int i = 0; i < n; i++)
      pos_glide_stroke_get_point (req->stroke, i, &xs[i], &ys[i], &times[i]);

    n_words = xxglide_decode (self->glide, xs, ys, times, n,
                              req->prev_word, &words, POS_GLIDE_MAX_WORDS);
    if (n_words > 0) {
      result->words = g_new0 (char *, n_words + 1);
      for (int i = 0; i < n_words; i++)
        result->words[i] = g_strdup (words[i]);
      xxglide_free_words (words, n_words);
    }
  }
  g_mutex_unlock (&self->lock);
#else
  (void) self;
#endif

  if (g_cancellable_is_cancelled (cancellable)) {
    pos_glide_result_free (result);
    g_task_return_new_error (task, G_IO_ERROR, G_IO_ERROR_CANCELLED, "Cancelled");
    return;
  }

  g_task_return_pointer (task, result, (GDestroyNotify) pos_glide_result_free);
}


static void
decode_done (GObject *source, GAsyncResult *res, gpointer user_data)
{
  g_autoptr (GError) err = NULL;
  PosGlideResult *result;
  DecodeNotify *notify = user_data;

  result = g_task_propagate_pointer (G_TASK (res), &err);
  if (result == NULL) {
    /* Cancelled or failed: report no candidates */
    g_debug ("Glide decode: %s", err ? err->message : "no result");
    notify->callback (POS_GLIDE_DECODER (source), NULL, notify->user_data);
  } else {
    notify->callback (POS_GLIDE_DECODER (source), result, notify->user_data);
    pos_glide_result_free (result);
  }
  g_free (notify);
}


void
pos_glide_decoder_decode (PosGlideDecoder *self, PosGlideStroke *stroke,
                          const char *prev_word, GCancellable *cancellable,
                          PosGlideDecodeCallback callback, gpointer user_data)
{
  GlideRequest *req;
  DecodeNotify *notify;
  g_autoptr (GTask) task = NULL;

  g_return_if_fail (POS_IS_GLIDE_DECODER (self));
  g_return_if_fail (stroke != NULL);
  g_return_if_fail (callback != NULL);

  req = g_new0 (GlideRequest, 1);
  req->stroke = pos_glide_stroke_copy (stroke);
  req->prev_word = g_strdup (prev_word);

  notify = g_new0 (DecodeNotify, 1);
  notify->callback = callback;
  notify->user_data = user_data;

  task = g_task_new (self, cancellable, decode_done, notify);
  g_task_set_task_data (task, req, (GDestroyNotify) glide_request_free);
  g_task_run_in_thread (task, decode_thread);
}
