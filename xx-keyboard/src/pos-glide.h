/*
 * Copyright (C) 2026 Phosh.mobi e.V.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#pragma once

#include <glib-object.h>
#include <gio/gio.h>
#include <gtk/gtk.h>

G_BEGIN_DECLS

/* Fraction of the key width the finger must move before a drag counts as
 * a glide. Mirrors Android's steady-state gesture distance threshold
 * (GestureStrokeRecognitionParams.mDynamicDistanceThresholdTo = 0.35 key
 * widths; the larger "from" value only applies right after fast typing,
 * which Stevia doesn't track).
 */
#define POS_GLIDE_START_DISTANCE_FRAC 0.35

/* How many candidates a decode returns at most: the committed word plus
 * alternates for the completion bar.
 */
#define POS_GLIDE_MAX_WORDS 5

#define POS_TYPE_GLIDE_STROKE (pos_glide_stroke_get_type ())

/* One glide gesture: finger positions in widget pixels with timestamps
 * in ms since the gesture began, collected at the native event rate.
 */
typedef struct _PosGlideStroke PosGlideStroke;

GType            pos_glide_stroke_get_type (void);
PosGlideStroke  *pos_glide_stroke_new (void);
PosGlideStroke  *pos_glide_stroke_copy (PosGlideStroke *self);
void             pos_glide_stroke_free (PosGlideStroke *self);
G_DEFINE_AUTOPTR_CLEANUP_FUNC (PosGlideStroke, pos_glide_stroke_free)
void             pos_glide_stroke_append (PosGlideStroke *self,
                                          float           x,
                                          float           y,
                                          int             time_ms);
int              pos_glide_stroke_get_n_points (PosGlideStroke *self);
void             pos_glide_stroke_get_point (PosGlideStroke *self,
                                             int             idx,
                                             float          *x,
                                             float          *y,
                                             int            *time_ms);

/* Whether the stroke moved far enough from its start to count as a glide.
 * @key_width is the key pitch in widget pixels.
 */
gboolean         pos_glide_stroke_exceeds_threshold (PosGlideStroke *self,
                                                    double          key_width);

/* The lowercase codepoint a key types, or 0 when the symbol isn't a
 * single letter (space, symbols, multi-char keys, …). Only letters are
 * passed to the decoder.
 */
gunichar         pos_glide_symbol_codepoint (const char *symbol);

/* One decoder key in widget pixels, mirroring XxGlideKey without
 * pulling the decoder header into every user.
 */
typedef struct {
  int   codepoint;
  float x, y, width, height;
} PosGlideKey;

/* Build the decoder key list from parallel symbol/box arrays. Letters
 * only; returns the keys (free with g_array_unref). @pitch is passed
 * through, it becomes most_common_key_width.
 */
GArray          *pos_glide_build_keys (const char        **symbols,
                                       const GdkRectangle *boxes,
                                       int                 n,
                                       double              pitch);

/* Text context helpers, @before is the field text before the cursor
 * (may be NULL). See the Android InputLogic gesture handling.
 */
gboolean         pos_glide_needs_leading_space (const char *before);
gboolean         pos_glide_is_sentence_start (const char *before);
char            *pos_glide_prev_word (const char *before);
char            *pos_glide_apply_capitalization (const char *word,
                                                 gboolean    capitalize);

/* Bytes the backspace-after-glide rule deletes: the whole glided word.
 * A separator space committed with it is kept, as on Android.
 */
gsize            pos_glide_pending_backspace_span (const char *glide_word);

#define POS_TYPE_GLIDE_DECODER (pos_glide_decoder_get_type ())
G_DECLARE_FINAL_TYPE (PosGlideDecoder, pos_glide_decoder, POS, GLIDE_DECODER, GObject)

/* Decode result, best candidate first (lowercase as in the dictionary). */
typedef struct {
  GStrv words;
} PosGlideResult;

void             pos_glide_result_free (PosGlideResult *result);

typedef void (*PosGlideDecodeCallback) (PosGlideDecoder *decoder,
                                        PosGlideResult  *result,
                                        gpointer         user_data);

/* Worker owning one XxGlide instance. Decoding runs on a GTask worker
 * thread so the UI never blocks; the instance is only ever touched
 * under the decoder's lock. @dict_dir holds main_en-US.dict and
 * main_en-GB.dict (<datadir>/xx-keyboard/dicts/).
 */
PosGlideDecoder *pos_glide_decoder_new (const char *dict_dir);
void             pos_glide_decoder_set_language (PosGlideDecoder *self,
                                                 const char      *lang,
                                                 const char      *region);
gboolean         pos_glide_decoder_get_available (PosGlideDecoder *self);
gboolean         pos_glide_decoder_set_layout (PosGlideDecoder *self,
                                               const PosGlideKey *keys,
                                               int              n_keys,
                                               int              width,
                                               int              height,
                                               int              pitch);
void             pos_glide_decoder_decode (PosGlideDecoder        *self,
                                           PosGlideStroke         *stroke,
                                           const char             *prev_word,
                                           GCancellable           *cancellable,
                                           PosGlideDecodeCallback  callback,
                                           gpointer                user_data);

G_END_DECLS
