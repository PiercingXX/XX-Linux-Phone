/*
 * Glide (swipe) decoder C API for xx-keyboard.
 *
 * Implemented in xxglide.cpp on top of the vendored AOSP LatinIME decoder
 * (latinime/, Apache-2.0, see NOTICE). Plain C, no glib in this header.
 *
 * Thread-safety: one XxGlide per thread; decode is synchronous (call it
 * from a worker thread, never the GTK main loop).
 */
#ifndef XXGLIDE_H
#define XXGLIDE_H

#ifdef __cplusplus
extern "C" {
#endif

typedef struct XxGlide XxGlide;

/* Load a LatinIME ver4 binary dictionary (main_en-US.dict). NULL on failure. */
XxGlide *xxglide_new (const char *dict_path);
void     xxglide_free (XxGlide *glide);

/* One key of the current layout, in surface pixels. codepoint is the lowercase
 * letter the key types ('a'..'z', '\'' etc.); non-letter keys are not passed. */
typedef struct {
  int   codepoint;
  float x, y, width, height;
} XxGlideKey;

/* Describe the visible letter layout. Call again whenever the layout or size
 * changes. Returns nonzero on success, 0 on invalid arguments. */
int xxglide_set_layout (XxGlide *glide, const XxGlideKey *keys, int n_keys,
                        int keyboard_width, int keyboard_height, int most_common_key_width);

/* Decode one gesture. Points in the same surface pixels, times in ms since the
 * gesture began. prev_word may be NULL (bigram context). Returns the number of
 * candidates written (best first, UTF-8, lowercase as in the dictionary);
 * caller frees with xxglide_free_words (). Returns 0 (and writes no words)
 * when there is no layout, no usable input, or no candidate. */
int  xxglide_decode (XxGlide *glide, const float *xs, const float *ys, const int *times_ms,
                     int n_points, const char *prev_word, char ***out_words, int max_words);
void xxglide_free_words (char **words, int n_words);

#ifdef __cplusplus
}
#endif

#endif /* XXGLIDE_H */
