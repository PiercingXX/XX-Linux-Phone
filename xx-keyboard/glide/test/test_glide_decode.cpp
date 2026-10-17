/* Glide decoder unit test: synthetic layouts and glides through the C API.
 *
 * Builds a QWERTY and a Colemak letter layout from plain rectangles, draws
 * synthetic glides by sampling straight segments through each letter centre
 * of five words, and checks the intended word comes back first. Malformed
 * input must return 0 without crashing.
 *
 * Dict / corpus paths come from -D defines set by meson.build.
 */
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "../xxglide.h"

static int failures = 0;

#define CHECK(cond) do { \
    if (!(cond)) { \
        printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        ++failures; \
    } \
} while (0)

struct KeyDef {
    char letter;
    float x, y;
};

struct Layout {
    std::vector<XxGlideKey> keys;
    int width = 0;
    int height = 0;
    int pitch = 0;
};

static Layout makeLayout(const char *const *rows, int n_rows) {
    const float keyW = 96, keyH = 140, gapX = 8, gapY = 12;
    const float pitch = keyW + gapX;
    Layout layout;
    for (int r = 0; r < n_rows; ++r) {
        const float rowLen = strlen(rows[r]);
        const float rowWidth = rowLen * keyW + (rowLen - 1) * gapX;
        const float x0 = (10 * keyW + 9 * gapX - rowWidth) / 2;
        for (size_t c = 0; c < rowLen; ++c) {
            XxGlideKey key;
            key.codepoint = rows[r][c];
            key.x = x0 + c * pitch;
            key.y = r * (keyH + gapY);
            key.width = keyW;
            key.height = keyH;
            layout.keys.push_back(key);
        }
    }
    layout.width = static_cast<int>(10 * keyW + 9 * gapX);
    layout.height = static_cast<int>(n_rows * keyH + (n_rows - 1) * gapY);
    layout.pitch = static_cast<int>(pitch);
    return layout;
}

static bool findCentre(const Layout &layout, char letter, float *x, float *y) {
    for (const XxGlideKey &k : layout.keys) {
        if (k.codepoint == letter) {
            *x = k.x + k.width / 2;
            *y = k.y + k.height / 2;
            return true;
        }
    }
    return false;
}

/* Sample straight segments through each letter centre every ~5 px. */
static void sampleGlide(const Layout &layout, const char *word, std::vector<float> *xs,
        std::vector<float> *ys, std::vector<int> *times) {
    float px = 0, py = 0;
    bool first = true;
    int t = 0;
    for (const char *c = word; *c; ++c) {
        float cx, cy;
        if (!findCentre(layout, *c, &cx, &cy)) {
            printf("letter %c missing from layout\n", *c);
            ++failures;
            return;
        }
        if (first) {
            xs->push_back(cx);
            ys->push_back(cy);
            times->push_back(t);
            first = false;
        } else {
            const float dx = cx - px, dy = cy - py;
            const float dist = std::hypot(dx, dy);
            const int steps = std::max(1, static_cast<int>(dist / 5));
            for (int i = 1; i <= steps; ++i) {
                xs->push_back(px + dx * i / steps);
                ys->push_back(py + dy * i / steps);
                t += 8;
                times->push_back(t);
            }
        }
        px = cx;
        py = cy;
    }
}

/* Case-insensitive: the dictionary stores some words capitalised ("Linux"),
 * and the decoder correctly returns the dictionary entry as-is. */
static int rankOf(char **words, int n, const char *want) {
    for (int i = 0; i < n; ++i) {
        const char *got = words[i];
        size_t k = 0;
        for (;;) {
            const int a = got[k], b = want[k];
            const int la = (a >= 'A' && a <= 'Z') ? a + 32 : a;
            const int lb = (b >= 'A' && b <= 'Z') ? b + 32 : b;
            if (la != lb) {
                break;
            }
            if (a == '\0') {
                return i;
            }
            ++k;
        }
    }
    return -1;
}

static void expectTop(XxGlide *glide, const Layout &layout, const char *name, const char *word,
        int worst_rank) {
    std::vector<float> xs, ys;
    std::vector<int> times;
    sampleGlide(layout, word, &xs, &ys, &times);
    char **out = nullptr;
    const int n = xxglide_decode(glide, xs.data(), ys.data(), times.data(),
            static_cast<int>(xs.size()), nullptr, &out, 5);
    const int rank = (n > 0 && out) ? rankOf(out, n, word) : -1;
    printf("%-8s %-9s ->", name, word);
    for (int i = 0; i < n && out; ++i) {
        printf(" %s", out[i]);
    }
    printf("  [rank %d]\n", rank);
    CHECK(rank >= 0 && rank <= worst_rank);
    xxglide_free_words(out, n > 0 ? n : 0);
}

static void malformed() {
    CHECK(xxglide_new(nullptr) == nullptr);
    CHECK(xxglide_new("") == nullptr);
    CHECK(xxglide_new("/nonexistent/main.dict") == nullptr);
    xxglide_free(nullptr);
    xxglide_free_words(nullptr, 0);
    xxglide_free_words(nullptr, 3);

    XxGlide *glide = xxglide_new(XXGLIDE_TEST_DICT);
    CHECK(glide != nullptr);
    if (!glide) {
        return;
    }
    XxGlideKey key = {'a', 0, 0, 96, 140};
    XxGlideKey outside = {'b', 900, 0, 96, 140};
    XxGlideKey zeroSize = {'c', 0, 0, 0, 140};
    CHECK(xxglide_set_layout(nullptr, &key, 1, 200, 200, 104) == 0);
    CHECK(xxglide_set_layout(glide, nullptr, 1, 200, 200, 104) == 0);
    CHECK(xxglide_set_layout(glide, &key, 0, 200, 200, 104) == 0);
    CHECK(xxglide_set_layout(glide, &key, 65, 200, 200, 104) == 0);
    CHECK(xxglide_set_layout(glide, &key, 1, 0, 200, 104) == 0);
    CHECK(xxglide_set_layout(glide, &key, 1, 200, 0, 104) == 0);
    CHECK(xxglide_set_layout(glide, &key, 1, 200, 200, 0) == 0);
    CHECK(xxglide_set_layout(glide, &outside, 1, 200, 200, 104) == 0);
    CHECK(xxglide_set_layout(glide, &zeroSize, 1, 200, 200, 104) == 0);

    float x = 10, y = 10;
    int t = 0;
    char **out = reinterpret_cast<char **>(0x1);
    /* No layout set yet: every decode returns 0 and nulls the output. */
    CHECK(xxglide_decode(glide, &x, &y, &t, 1, nullptr, &out, 5) == 0);
    CHECK(out == nullptr);
    CHECK(xxglide_decode(nullptr, &x, &y, &t, 1, nullptr, &out, 5) == 0);
    CHECK(xxglide_decode(glide, nullptr, &y, &t, 1, nullptr, &out, 5) == 0);
    CHECK(xxglide_decode(glide, &x, nullptr, &t, 1, nullptr, &out, 5) == 0);
    CHECK(xxglide_decode(glide, &x, &y, nullptr, 1, nullptr, &out, 5) == 0);
    CHECK(xxglide_decode(glide, &x, &y, &t, 0, nullptr, &out, 5) == 0);
    CHECK(xxglide_decode(glide, &x, &y, &t, 1, nullptr, nullptr, 5) == 0);
    CHECK(xxglide_decode(glide, &x, &y, &t, 1, nullptr, &out, 0) == 0);
    CHECK(out == nullptr);

    CHECK(xxglide_set_layout(glide, &key, 1, 200, 200, 104) != 0);
    /* Points far outside the surface and a non-finite point still decode or
     * return 0 without crashing. */
    float far[] = {5000, -300};
    float farY[] = {5000, -300};
    int farT[] = {0, 8};
    out = nullptr;
    const int n = xxglide_decode(glide, far, farY, farT, 2, nullptr, &out, 5);
    CHECK(n >= 0);
    xxglide_free_words(out, n > 0 ? n : 0);
    const float nan = std::nanf("");
    out = nullptr;
    CHECK(xxglide_decode(glide, &nan, &y, &t, 1, nullptr, &out, 5) == 0);
    CHECK(out == nullptr);

    /* Bigram context is exercised, not asserted for content. */
    CHECK(xxglide_set_layout(glide, &key, 1, 200, 200, 104) != 0);
    out = nullptr;
    const int b = xxglide_decode(glide, &x, &y, &t, 1, "the", &out, 5);
    CHECK(b >= 0);
    xxglide_free_words(out, b > 0 ? b : 0);

    xxglide_free(glide);
}

int main() {
    malformed();
    if (failures) {
        return 1;
    }

    static const char *qwerty[] = {"qwertyuiop", "asdfghjkl", "zxcvbnm"};
    static const char *colemak[] = {"qwfpgjluy;", "arstdhneio", "zxcvbkm"};
    static const char *words[] = {"hello", "the", "keyboard", "linux", "phone"};

    XxGlide *glide = xxglide_new(XXGLIDE_TEST_DICT);
    CHECK(glide != nullptr);
    if (!glide) {
        return 1;
    }
    for (int layout_idx = 0; layout_idx < 2; ++layout_idx) {
        const Layout layout = layout_idx == 0 ? makeLayout(qwerty, 3) : makeLayout(colemak, 3);
        const char *name = layout_idx == 0 ? "qwerty" : "colemak";
        CHECK(xxglide_set_layout(glide, layout.keys.data(),
                static_cast<int>(layout.keys.size()), layout.width, layout.height,
                layout.pitch) != 0);
        for (const char *word : words) {
            expectTop(glide, layout, name, word, 0);
        }
        /* Re-setting the same layout must keep working. */
        CHECK(xxglide_set_layout(glide, layout.keys.data(),
                static_cast<int>(layout.keys.size()), layout.width, layout.height,
                layout.pitch) != 0);
    }
    xxglide_free(glide);

    if (failures) {
        printf("glide decode test: %d failures\n", failures);
        return 1;
    }
    printf("glide decode test: ok\n");
    return 0;
}
