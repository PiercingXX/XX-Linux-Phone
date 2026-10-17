/*
 * Copyright (C) 2026 Phosh.mobi e.V.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Widget-level glide tests: gesture capture, tap-vs-glide thresholds and
 * the layout-to-decoder path on a real us layout. Needs a display
 * (e.g. Broadway); skips cleanly without one.
 */

#include "pos-osk-widget.c"

#include "pos-glide.h"
#include "pos-main.h"

#include <glib.h>
#include <math.h>

static int n_key_symbols;
static char last_symbol[32];
static int n_glides;
static PosGlideStroke *last_stroke;


static void
on_key_symbol (PosOskWidget *widget, const char *symbol, gpointer data)
{
  n_key_symbols++;
  g_strlcpy (last_symbol, symbol, sizeof (last_symbol));
}


static void
on_glide (PosOskWidget *widget, PosGlideStroke *stroke, gpointer data)
{
  n_glides++;
  g_clear_pointer (&last_stroke, pos_glide_stroke_free);
  last_stroke = pos_glide_stroke_copy (stroke);
}


static gboolean
find_key_center (PosOskWidget *widget, const char *symbol, double *x, double *y)
{
  for (int r = 0; r < widget->layout.n_rows; r++) {
    PosOskWidgetRow *row = pos_osk_widget_get_layer_row (widget,
                                                         POS_OSK_WIDGET_LAYER_NORMAL,
                                                         r);

    for (int k = 0; k < pos_osk_widget_row_get_num_keys (row); k++) {
      PosOskKey *key = pos_osk_widget_row_get_key (row, k);

      if (g_strcmp0 (pos_osk_key_get_symbol (key), symbol) == 0) {
        const GdkRectangle *box = pos_osk_key_get_box (key);

        *x = box->x + box->width / 2.0;
        *y = box->y + box->height / 2.0;
        return TRUE;
      }
    }
  }

  return FALSE;
}


static GdkEventTouch
make_touch (GdkEventType type, double x, double y, guint32 time)
{
  GdkEventTouch ev = { 0 };

  ev.type = type;
  ev.sequence = GUINT_TO_POINTER (42);
  ev.x = x;
  ev.y = y;
  ev.time = time;

  return ev;
}


static void
tap (PosOskWidget *widget, double x, double y, guint32 *time)
{
  GdkEventTouch ev;

  ev = make_touch (GDK_TOUCH_BEGIN, x, y, *time);
  pos_osk_widget_touch_event (GTK_WIDGET (widget), &ev);
  *time += 50;
  ev = make_touch (GDK_TOUCH_END, x, y, *time);
  pos_osk_widget_touch_event (GTK_WIDGET (widget), &ev);
  *time += 50;
}


typedef struct {
  PosOskWidget *widget;
  guint32       time;
} Fixture;


static Fixture
make_fixture (void)
{
  Fixture f = { NULL, 1000 };
  g_autoptr (GError) err = NULL;
  g_autofree char *dict_dir = g_path_get_dirname (XXGLIDE_TEST_DICT);
  g_autoptr (PosGlideDecoder) decoder = pos_glide_decoder_new (dict_dir);
  GtkAllocation alloc = { 0, 0, 360, 200 };
  gboolean ok;

  f.widget = pos_osk_widget_new (PHOSH_OSK_FEATURE_DEFAULT);
  ok = pos_osk_widget_set_layout (f.widget, "us", "us", "English (US)", "us", NULL, &err);
  g_assert_no_error (err);
  g_assert_true (ok);

  pos_glide_decoder_set_language (decoder,
                                  pos_osk_widget_get_lang (f.widget),
                                  pos_osk_widget_get_region (f.widget));
  g_assert_true (pos_glide_decoder_get_available (decoder));
  pos_osk_widget_set_glide_decoder (f.widget, decoder);

  /* size_allocate computes the key boxes and pushes the glide layout.
   * Called directly: without a shown window GTK never allocates.
   */
  gtk_widget_show (GTK_WIDGET (f.widget));
  pos_osk_widget_size_allocate (GTK_WIDGET (f.widget), &alloc);

  g_signal_connect (f.widget, "key-symbol", G_CALLBACK (on_key_symbol), NULL);
  g_signal_connect (f.widget, "glide", G_CALLBACK (on_glide), NULL);

  n_key_symbols = 0;
  n_glides = 0;
  last_symbol[0] = '\0';
  g_clear_pointer (&last_stroke, pos_glide_stroke_free);

  return f;
}


static void
test_tap (void)
{
  Fixture f = make_fixture ();
  double x, y;

  g_assert_true (find_key_center (f.widget, "h", &x, &y));
  tap (f.widget, x, y, &f.time);

  g_assert_cmpint (n_key_symbols, ==, 1);
  g_assert_cmpstr (last_symbol, ==, "h");
  g_assert_cmpint (n_glides, ==, 0);

  g_object_ref_sink (f.widget);
  g_object_unref (f.widget);
}


/* A wiggle inside the start key is still a tap */
static void
test_wiggle (void)
{
  Fixture f = make_fixture ();
  GdkEventTouch ev;
  double x, y;

  g_assert_true (find_key_center (f.widget, "h", &x, &y));

  ev = make_touch (GDK_TOUCH_BEGIN, x, y, f.time);
  pos_osk_widget_touch_event (GTK_WIDGET (f.widget), &ev);
  ev = make_touch (GDK_TOUCH_UPDATE, x + 3, y + 2, f.time + 10);
  pos_osk_widget_touch_event (GTK_WIDGET (f.widget), &ev);
  ev = make_touch (GDK_TOUCH_END, x + 3, y + 2, f.time + 60);
  pos_osk_widget_touch_event (GTK_WIDGET (f.widget), &ev);

  g_assert_cmpint (n_key_symbols, ==, 1);
  g_assert_cmpint (n_glides, ==, 0);

  g_object_ref_sink (f.widget);
  g_object_unref (f.widget);
}


/* Drag on the space bar: no glide, cursor-drag behaviour kept */
static void
test_space_drag (void)
{
  Fixture f = make_fixture ();
  GdkEventTouch ev;
  double x, y;

  g_assert_true (find_key_center (f.widget, " ", &x, &y));

  ev = make_touch (GDK_TOUCH_BEGIN, x, y, f.time);
  pos_osk_widget_touch_event (GTK_WIDGET (f.widget), &ev);
  /* Well off the key (and the keyboard): a cursor drag, never a glide */
  ev = make_touch (GDK_TOUCH_UPDATE, x + 260, y, f.time + 10);
  pos_osk_widget_touch_event (GTK_WIDGET (f.widget), &ev);
  ev = make_touch (GDK_TOUCH_END, x + 260, y, f.time + 60);
  pos_osk_widget_touch_event (GTK_WIDGET (f.widget), &ev);

  g_assert_cmpint (n_glides, ==, 0);
  g_assert_cmpint (n_key_symbols, ==, 0);

  g_object_ref_sink (f.widget);
  g_object_unref (f.widget);
}


/* Sample straight segments through letter centres, ~5px apart */
static void
trace_word (PosOskWidget *widget, const char *word, guint32 *time)
{
  double px = 0, py = 0;
  gboolean first = TRUE;
  GdkEventTouch ev;

  for (const char *c = word; *c; c++) {
    char sym[2] = { *c, '\0' };
    double cx, cy;

    g_assert_true (find_key_center (widget, sym, &cx, &cy));

    if (first) {
      ev = make_touch (GDK_TOUCH_BEGIN, cx, cy, *time);
      pos_osk_widget_touch_event (GTK_WIDGET (widget), &ev);
      first = FALSE;
    } else {
      double dist = hypot (cx - px, cy - py);
      int steps = MAX (1, (int) (dist / 5));

      for (int i = 1; i <= steps; i++) {
        *time += 8;
        ev = make_touch (GDK_TOUCH_UPDATE,
                         px + (cx - px) * i / steps,
                         py + (cy - py) * i / steps,
                         *time);
        pos_osk_widget_touch_event (GTK_WIDGET (widget), &ev);
      }
    }
    px = cx;
    py = cy;
  }

  *time += 8;
  ev = make_touch (GDK_TOUCH_END, px, py, *time);
  pos_osk_widget_touch_event (GTK_WIDGET (widget), &ev);
}


/* A drag across letter keys becomes a glide, not taps */
static void
test_glide_capture (void)
{
  Fixture f = make_fixture ();

  trace_word (f.widget, "hello", &f.time);

  g_assert_cmpint (n_glides, ==, 1);
  g_assert_cmpint (n_key_symbols, ==, 0);
  g_assert_nonnull (last_stroke);
  /* Points at the native event rate, ms since start */
  g_assert_cmpint (pos_glide_stroke_get_n_points (last_stroke), >, 10);

  g_object_ref_sink (f.widget);
  g_object_unref (f.widget);
}


/* With glide disabled the same drag behaves as upstream: cancelled */
static void
test_glide_disabled (void)
{
  Fixture f = make_fixture ();

  pos_osk_widget_set_glide_enabled (f.widget, FALSE);
  trace_word (f.widget, "hello", &f.time);

  g_assert_cmpint (n_glides, ==, 0);
  g_assert_cmpint (n_key_symbols, ==, 0);

  g_object_ref_sink (f.widget);
  g_object_unref (f.widget);
}


typedef struct {
  GMainLoop *loop;
  GStrv      words;
} DecodeState;


static void
on_decoded (PosGlideDecoder *decoder, PosGlideResult *result, gpointer user_data)
{
  DecodeState *state = user_data;

  if (result != NULL && result->words != NULL)
    state->words = g_strdupv (result->words);
  g_main_loop_quit (state->loop);
}


static gboolean
on_decode_timeout (gpointer user_data)
{
  g_main_loop_quit ((GMainLoop *) user_data);
  return G_SOURCE_REMOVE;
}


/* The widget's key geometry decodes: a trace through h-e-l-l-o ranks
 * "hello" first through the worker, as in the decoder's own tests.
 */
static void
test_glide_decode_layout (void)
{
  Fixture f = make_fixture ();
  g_autofree char *dict_dir = g_path_get_dirname (XXGLIDE_TEST_DICT);
  g_autoptr (PosGlideDecoder) decoder = pos_glide_decoder_new (dict_dir);
  g_autoptr (GMainLoop) loop = g_main_loop_new (NULL, FALSE);
  g_autoptr (GCancellable) cancel = g_cancellable_new ();
  DecodeState state = { loop, NULL };
  PosOskWidgetKeyboardLayer *layer;

  trace_word (f.widget, "hello", &f.time);
  g_assert_cmpint (n_glides, ==, 1);
  g_assert_nonnull (last_stroke);

  /* The widget pushed this same layout on size_allocate; mirror the
   * pitch the widget used so the decode matches the live path.
   */
  layer = pos_osk_widget_get_keyboard_layer (f.widget, POS_OSK_WIDGET_LAYER_NORMAL);
  pos_glide_decoder_set_language (decoder, "en", "us");
  g_assert_true (pos_glide_decoder_get_available (decoder));

  {
    /* Rebuild the key list the way the widget does and check the pitch.
     * Symbols are borrowed from the keys; only the array is owned.
     */
    g_autoptr (GArray) keys = NULL;
    g_autofree const char **symbols = NULL;
    g_autofree GdkRectangle *boxes = NULL;
    int n = 0;

    for (int r = 0; r < f.widget->layout.n_rows; r++) {
      PosOskWidgetRow *row = pos_osk_widget_get_layer_row (f.widget,
                                                           POS_OSK_WIDGET_LAYER_NORMAL,
                                                           r);
      n += pos_osk_widget_row_get_num_keys (row);
    }
    g_assert_cmpint (n, >, 20);

    symbols = g_new0 (const char *, n);
    boxes = g_new0 (GdkRectangle, n);
    n = 0;
    for (int r = 0; r < f.widget->layout.n_rows; r++) {
      PosOskWidgetRow *row = pos_osk_widget_get_layer_row (f.widget,
                                                           POS_OSK_WIDGET_LAYER_NORMAL,
                                                           r);

      for (int k = 0; k < pos_osk_widget_row_get_num_keys (row); k++) {
        PosOskKey *key = pos_osk_widget_row_get_key (row, k);

        symbols[n] = pos_osk_key_get_symbol (key);
        boxes[n] = *pos_osk_key_get_box (key);
        n++;
      }
    }
    keys = pos_glide_build_keys (symbols, boxes, n, layer->key_width);
    g_assert_cmpuint (keys->len, >, 20);
    g_assert_true (pos_glide_decoder_set_layout (decoder,
                                                 (PosGlideKey *) keys->data, keys->len,
                                                 360, 200,
                                                 (int) round (layer->key_width)));
  }

  pos_glide_decoder_decode (decoder, last_stroke, NULL, cancel, on_decoded, &state);
  g_timeout_add (15000, on_decode_timeout, loop);
  g_main_loop_run (loop);

  g_assert_nonnull (state.words);
  g_assert_nonnull (state.words[0]);
  g_assert_cmpstr (state.words[0], ==, "hello");
  g_strfreev (state.words);

  g_object_ref_sink (f.widget);
  g_object_unref (f.widget);
}


int
main (int argc, char *argv[])
{
  int ret;

  g_test_init (&argc, &argv, NULL);

  if (!gtk_init_check (&argc, &argv)) {
    g_test_skip ("No display, skipping widget glide tests");
    return 0;
  }

  pos_init ();

  g_test_add_func ("/pos/glide-widget/tap", test_tap);
  g_test_add_func ("/pos/glide-widget/wiggle", test_wiggle);
  g_test_add_func ("/pos/glide-widget/space-drag", test_space_drag);
  g_test_add_func ("/pos/glide-widget/capture", test_glide_capture);
  g_test_add_func ("/pos/glide-widget/disabled", test_glide_disabled);
  g_test_add_func ("/pos/glide-widget/decode-layout", test_glide_decode_layout);

  ret = g_test_run ();

  pos_uninit ();
  return ret;
}
