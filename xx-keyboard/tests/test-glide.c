/*
 * Copyright (C) 2026 Phosh.mobi e.V.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Unit tests for the pure glide helpers in pos-glide.c. No display needed.
 */

#include "pos-glide.h"

#include <glib.h>


static void
test_stroke (void)
{
  PosGlideStroke *stroke = pos_glide_stroke_new ();
  PosGlideStroke *copy;
  float x, y;
  int t;

  g_assert_cmpint (pos_glide_stroke_get_n_points (stroke), ==, 0);
  /* A single point never exceeds the threshold */
  pos_glide_stroke_append (stroke, 0, 0, 0);
  g_assert_false (pos_glide_stroke_exceeds_threshold (stroke, 40));

  /* Android steady-state threshold: 0.35 key widths */
  pos_glide_stroke_append (stroke, 13, 0, 10);
  g_assert_false (pos_glide_stroke_exceeds_threshold (stroke, 40));
  pos_glide_stroke_append (stroke, 14, 0, 20);
  g_assert_true (pos_glide_stroke_exceeds_threshold (stroke, 40));

  /* Diagonal distance counts too */
  g_assert_true (pos_glide_stroke_exceeds_threshold (stroke, 20));

  pos_glide_stroke_get_point (stroke, 2, &x, &y, &t);
  g_assert_cmpfloat (x, ==, 14);
  g_assert_cmpfloat (y, ==, 0);
  g_assert_cmpint (t, ==, 20);

  copy = pos_glide_stroke_copy (stroke);
  g_assert_cmpint (pos_glide_stroke_get_n_points (copy), ==, 3);
  pos_glide_stroke_free (stroke);
  pos_glide_stroke_get_point (copy, 0, &x, &y, &t);
  g_assert_cmpfloat (x, ==, 0);
  g_assert_cmpint (t, ==, 0);
  pos_glide_stroke_free (copy);
}


static void
test_codepoint (void)
{
  g_assert_cmpuint (pos_glide_symbol_codepoint ("a"), ==, 'a');
  g_assert_cmpuint (pos_glide_symbol_codepoint ("A"), ==, 'a');
  g_assert_cmpuint (pos_glide_symbol_codepoint ("ä"), ==, 0xe4);
  g_assert_cmpuint (pos_glide_symbol_codepoint (NULL), ==, 0);
  g_assert_cmpuint (pos_glide_symbol_codepoint (""), ==, 0);
  g_assert_cmpuint (pos_glide_symbol_codepoint (" "), ==, 0);
  g_assert_cmpuint (pos_glide_symbol_codepoint ("1"), ==, 0);
  g_assert_cmpuint (pos_glide_symbol_codepoint ("'"), ==, 0);
  g_assert_cmpuint (pos_glide_symbol_codepoint ("ab"), ==, 0);
  g_assert_cmpuint (pos_glide_symbol_codepoint ("KEY_BACKSPACE"), ==, 0);
  g_assert_cmpuint (pos_glide_symbol_codepoint ("←"), ==, 0);
}


static void
test_build_keys (void)
{
  const char *symbols[] = { "a", " ", "KEY_BACKSPACE", "B", "é", "ab" };
  GdkRectangle boxes[] = {
    { 0, 0, 40, 50 }, { 40, 0, 80, 50 }, { 120, 0, 40, 50 },
    { 0, 50, 40, 50 }, { 40, 50, 40, 50 }, { 80, 50, 40, 50 },
  };
  g_autoptr (GArray) keys = pos_glide_build_keys (symbols, boxes, 6, 40.0);
  PosGlideKey *k;

  /* Letters only, in order */
  g_assert_cmpuint (keys->len, ==, 3);

  k = &g_array_index (keys, PosGlideKey, 0);
  g_assert_cmpint (k->codepoint, ==, 'a');
  g_assert_cmpfloat (k->x, ==, 0);
  g_assert_cmpfloat (k->y, ==, 0);
  g_assert_cmpfloat (k->width, ==, 40);
  g_assert_cmpfloat (k->height, ==, 50);

  k = &g_array_index (keys, PosGlideKey, 1);
  g_assert_cmpint (k->codepoint, ==, 'b');

  k = &g_array_index (keys, PosGlideKey, 2);
  g_assert_cmpint (k->codepoint, ==, 0xe9);
  g_assert_cmpfloat (k->x, ==, 40);
  g_assert_cmpfloat (k->y, ==, 50);
}


static void
test_leading_space (void)
{
  g_assert_false (pos_glide_needs_leading_space (NULL));
  g_assert_false (pos_glide_needs_leading_space (""));
  g_assert_false (pos_glide_needs_leading_space ("hi "));
  g_assert_false (pos_glide_needs_leading_space ("hi\n"));
  g_assert_false (pos_glide_needs_leading_space (" "));
  g_assert_true (pos_glide_needs_leading_space ("hi"));
  g_assert_true (pos_glide_needs_leading_space ("hi\t"));
  g_assert_true (pos_glide_needs_leading_space ("("));
  g_assert_true (pos_glide_needs_leading_space ("héllo"));
}


static void
test_sentence_start (void)
{
  g_assert_true (pos_glide_is_sentence_start (NULL));
  g_assert_true (pos_glide_is_sentence_start (""));
  g_assert_true (pos_glide_is_sentence_start ("   "));
  g_assert_true (pos_glide_is_sentence_start ("Hi. "));
  g_assert_true (pos_glide_is_sentence_start ("Hi! "));
  g_assert_true (pos_glide_is_sentence_start ("Really?  "));
  g_assert_true (pos_glide_is_sentence_start ("line\n"));
  g_assert_true (pos_glide_is_sentence_start ("e.g. "));
  g_assert_false (pos_glide_is_sentence_start ("hi"));
  g_assert_false (pos_glide_is_sentence_start ("hi "));
  g_assert_false (pos_glide_is_sentence_start ("e.g. x"));
  g_assert_false (pos_glide_is_sentence_start ("3.14"));
}


static void
test_prev_word (void)
{
  g_autofree char *w = NULL;

  g_assert_null (pos_glide_prev_word (NULL));
  g_assert_null (pos_glide_prev_word (""));
  g_assert_null (pos_glide_prev_word ("   "));
  g_assert_null (pos_glide_prev_word ("123"));

  w = pos_glide_prev_word ("hello");
  g_assert_cmpstr (w, ==, "hello");
  g_clear_pointer (&w, g_free);

  w = pos_glide_prev_word ("Say Hello");
  g_assert_cmpstr (w, ==, "hello");
  g_clear_pointer (&w, g_free);

  w = pos_glide_prev_word ("hello ");
  g_assert_cmpstr (w, ==, "hello");
  g_clear_pointer (&w, g_free);

  w = pos_glide_prev_word ("hi!");
  g_assert_cmpstr (w, ==, "hi");
  g_clear_pointer (&w, g_free);

  w = pos_glide_prev_word ("don't");
  g_assert_cmpstr (w, ==, "don't");
  g_clear_pointer (&w, g_free);
}


static void
test_capitalization (void)
{
  g_autofree char *plain = pos_glide_apply_capitalization ("hello", FALSE);
  g_autofree char *capped = pos_glide_apply_capitalization ("hello", TRUE);
  g_autofree char *wide = pos_glide_apply_capitalization ("éclair", TRUE);

  g_assert_cmpstr (plain, ==, "hello");
  g_assert_cmpstr (capped, ==, "Hello");
  g_assert_cmpstr (wide, ==, "Éclair");
}


static void
test_backspace_span (void)
{
  /* The whole glided word goes; a committed separator space stays */
  g_assert_cmpuint (pos_glide_pending_backspace_span ("hello"), ==, 5);
  g_assert_cmpuint (pos_glide_pending_backspace_span ("héllo"), ==, 6);
}


typedef struct {
  GMainLoop      *loop;
  PosGlideResult *result;
} DecodeState;


static void
on_decoded (PosGlideDecoder *decoder, PosGlideResult *result, gpointer user_data)
{
  DecodeState *state = user_data;

  if (result != NULL && result->words != NULL) {
    int n = g_strv_length (result->words);

    state->result = g_new0 (PosGlideResult, 1);
    state->result->words = g_new0 (char *, n + 1);
    for (int i = 0; i < n; i++)
      state->result->words[i] = g_strdup (result->words[i]);
  }
  g_main_loop_quit (state->loop);
}


static gboolean
on_decode_timeout (gpointer user_data)
{
  g_main_loop_quit ((GMainLoop *) user_data);
  return G_SOURCE_REMOVE;
}


/* Decoding without a dictionary reports no candidates instead of crashing */
static void
test_decoder_no_dict (void)
{
  g_autoptr (PosGlideDecoder) decoder = pos_glide_decoder_new ("/nonexistent");
  g_autoptr (PosGlideStroke) stroke = pos_glide_stroke_new ();
  g_autoptr (GMainLoop) loop = g_main_loop_new (NULL, FALSE);
  g_autoptr (GCancellable) cancel = g_cancellable_new ();
  DecodeState state = { loop, NULL };
  PosGlideKey key = { 'a', 0, 0, 40, 50 };

  pos_glide_decoder_set_language (decoder, "en", "us");
  g_assert_false (pos_glide_decoder_get_available (decoder));
  g_assert_true (pos_glide_decoder_set_layout (decoder, &key, 1, 360, 200, 40));

  pos_glide_stroke_append (stroke, 20, 25, 0);
  pos_glide_stroke_append (stroke, 60, 25, 10);
  pos_glide_decoder_decode (decoder, stroke, NULL, cancel, on_decoded, &state);

  g_timeout_add (5000, on_decode_timeout, loop);
  g_main_loop_run (loop);
  g_assert_null (state.result);
}


static void
test_decoder_language (void)
{
  g_autofree char *dir = g_path_get_dirname (XXGLIDE_TEST_DICT);
  g_autoptr (PosGlideDecoder) decoder = pos_glide_decoder_new (dir);

  /* en-US by default … */
  pos_glide_decoder_set_language (decoder, "en", "us");
  g_assert_true (pos_glide_decoder_get_available (decoder));

  /* … en-GB when the layout says so, both dictionaries ship … */
  pos_glide_decoder_set_language (decoder, "en", "gb");
  g_assert_true (pos_glide_decoder_get_available (decoder));

  /* … anything else falls back to en-US */
  pos_glide_decoder_set_language (decoder, "de", "de");
  g_assert_true (pos_glide_decoder_get_available (decoder));
}


int
main (int argc, char *argv[])
{
  g_test_init (&argc, &argv, NULL);

  g_test_add_func ("/pos/glide/stroke", test_stroke);
  g_test_add_func ("/pos/glide/codepoint", test_codepoint);
  g_test_add_func ("/pos/glide/build-keys", test_build_keys);
  g_test_add_func ("/pos/glide/leading-space", test_leading_space);
  g_test_add_func ("/pos/glide/sentence-start", test_sentence_start);
  g_test_add_func ("/pos/glide/prev-word", test_prev_word);
  g_test_add_func ("/pos/glide/capitalization", test_capitalization);
  g_test_add_func ("/pos/glide/backspace-span", test_backspace_span);
  g_test_add_func ("/pos/glide/decoder-no-dict", test_decoder_no_dict);
  g_test_add_func ("/pos/glide/decoder-language", test_decoder_language);

  return g_test_run ();
}
