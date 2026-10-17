/*
 * xxglide.cpp: plain-C glide decoder API on the vendored LatinIME decoder.
 *
 * Layout handling mirrors the Kotlin side (Keyboard -> ProximityInfo):
 * key centres from the key rectangles, the 32x16 proximity grid from
 * config_keyboard_grid_width/height, and null sweet-spot arrays, which is
 * what an uncalibrated layout feeds (key centres are then used as-is).
 * most_common_key_width scales the decoder's spatial sigmas; the matching
 * height is the modal key height. Decoding mirrors BatchInputArbiter ->
 * BinaryDictionary gesture path: isGesture suggest options, pointer ids
 * all zero, NOT_A_CODE_POINT input codes, no bigram context unless
 * prev_word is given.
 */

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <memory>
#include <string>
#include <sys/stat.h>
#include <vector>

#include "xxglide.h"

/* Minimal host JNIEnv: the decoder paths used here only call these. */
#include "jni.h"

namespace {

struct HostArray {
    std::vector<int> ints;
    jsize length() const { return static_cast<jsize>(ints.size()); }
};

jsize shimGetArrayLength(JNIEnv *, jarray array) {
    return reinterpret_cast<const HostArray *>(array)->length();
}

void shimGetIntArrayRegion(JNIEnv *, jintArray array, jsize start, jsize len, jint *buf) {
    const HostArray *a = reinterpret_cast<const HostArray *>(array);
    if (len > 0) {
        memcpy(buf, a->ints.data() + start, static_cast<size_t>(len) * sizeof(jint));
    }
}

void shimGetFloatArrayRegion(JNIEnv *, jfloatArray, jsize, jsize, jfloat *) {}

jclass shimFindClass(JNIEnv *, const char *) { return nullptr; }
void shimExceptionClear(JNIEnv *) {}

JNIEnv *makeHostJniEnv() {
    static JNINativeInterface_ iface;
    static JNIEnv env;
    memset(&iface, 0, sizeof(iface));
    iface.GetArrayLength = shimGetArrayLength;
    iface.GetIntArrayRegion = shimGetIntArrayRegion;
    iface.GetFloatArrayRegion = shimGetFloatArrayRegion;
    iface.FindClass = shimFindClass;
    iface.ExceptionClear = shimExceptionClear;
    env.functions = &iface;
    return &env;
}

} // namespace

#include "defines.h"
#include "dictionary/property/ngram_context.h"
#include "dictionary/structure/dictionary_structure_with_buffer_policy_factory.h"
#include "suggest/core/dictionary/dictionary.h"
#include "suggest/core/layout/proximity_info.h"
#include "suggest/core/result/suggestion_results.h"
#include "suggest/core/session/dic_traverse_session.h"
#include "suggest/core/suggest_options.h"

using namespace latinime;

struct XxGlide {
    Dictionary *dict = nullptr;
    std::unique_ptr<ProximityInfo> proximity;
    int keyboardWidth = 0;
    int keyboardHeight = 0;
};

namespace {

/* Grid size the Kotlin ProximityInfo is built with
 * (config_keyboard_grid_width/height). */
const int kGridWidth = 32;
const int kGridHeight = 16;

int modalOf(const std::vector<int> &values, int fallback) {
    if (values.empty()) {
        return fallback;
    }
    std::map<int, int> counts;
    for (int v : values) {
        ++counts[v];
    }
    int best = values[0];
    int bestCount = 0;
    for (const auto &kv : counts) {
        if (kv.second > bestCount) {
            bestCount = kv.second;
            best = kv.first;
        }
    }
    return best;
}

std::string encodeUtf8(const int *codePoints, int count) {
    std::string out;
    for (int i = 0; i < count; ++i) {
        const unsigned int c = static_cast<unsigned int>(codePoints[i]);
        if (c < 0x80) {
            out += static_cast<char>(c);
        } else if (c < 0x800) {
            out += static_cast<char>(0xC0 | (c >> 6));
            out += static_cast<char>(0x80 | (c & 0x3F));
        } else if (c < 0x10000) {
            out += static_cast<char>(0xE0 | (c >> 12));
            out += static_cast<char>(0x80 | ((c >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (c & 0x3F));
        } else {
            out += static_cast<char>(0xF0 | (c >> 18));
            out += static_cast<char>(0x80 | ((c >> 12) & 0x3F));
            out += static_cast<char>(0x80 | ((c >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (c & 0x3F));
        }
    }
    return out;
}

std::vector<int> decodeUtf8Lower(const char *s) {
    std::vector<int> out;
    const auto lowerAscii = [](int c) { return (c >= 'A' && c <= 'Z') ? c + 32 : c; };
    for (const unsigned char *p = reinterpret_cast<const unsigned char *>(s); *p;) {
        if (*p < 0x80) {
            out.push_back(lowerAscii(*p++));
        } else if ((*p & 0xE0) == 0xC0 && p[1]) {
            out.push_back(((p[0] & 0x1F) << 6) | (p[1] & 0x3F));
            p += 2;
        } else if ((*p & 0xF0) == 0xE0 && p[1] && p[2]) {
            out.push_back(((p[0] & 0x0F) << 12) | ((p[1] & 0x3F) << 6) | (p[2] & 0x3F));
            p += 3;
        } else if ((*p & 0xF8) == 0xF0 && p[1] && p[2] && p[3]) {
            out.push_back(((p[0] & 0x07) << 18) | ((p[1] & 0x3F) << 12)
                    | ((p[2] & 0x3F) << 6) | (p[3] & 0x3F));
            p += 4;
        } else {
            ++p;
        }
    }
    return out;
}

} // namespace

XxGlide *xxglide_new(const char *dict_path) {
    if (!dict_path || !*dict_path) {
        return nullptr;
    }
    struct stat st;
    if (stat(dict_path, &st) != 0 || st.st_size <= 0) {
        return nullptr;
    }
    JNIEnv *env = makeHostJniEnv();
    auto policy = DictionaryStructureWithBufferPolicyFactory::newPolicyForExistingDictFile(
            dict_path, 0, static_cast<int>(st.st_size), false /* isUpdatable */);
    if (!policy) {
        return nullptr;
    }
    std::unique_ptr<Dictionary> dict(new Dictionary(env, std::move(policy)));
    XxGlide *glide = new (std::nothrow) XxGlide();
    if (!glide) {
        return nullptr;
    }
    glide->dict = dict.release();
    return glide;
}

void xxglide_free(XxGlide *glide) {
    if (!glide) {
        return;
    }
    delete glide->dict;
    delete glide;
}

int xxglide_set_layout(XxGlide *glide, const XxGlideKey *keys, int n_keys,
        int keyboard_width, int keyboard_height, int most_common_key_width) {
    if (!glide || !glide->dict || !keys || n_keys <= 0 || n_keys > MAX_KEY_COUNT_IN_A_KEYBOARD
            || keyboard_width <= 0 || keyboard_height <= 0 || most_common_key_width <= 0) {
        return 0;
    }
    std::vector<int> xs, ys, ws, hs, codes, heights;
    xs.reserve(n_keys);
    ys.reserve(n_keys);
    ws.reserve(n_keys);
    hs.reserve(n_keys);
    codes.reserve(n_keys);
    heights.reserve(n_keys);
    for (int i = 0; i < n_keys; ++i) {
        const int x = static_cast<int>(std::lround(keys[i].x));
        const int y = static_cast<int>(std::lround(keys[i].y));
        const int w = static_cast<int>(std::lround(keys[i].width));
        const int h = static_cast<int>(std::lround(keys[i].height));
        /* Keys must sit inside the keyboard surface. */
        if (w <= 0 || h <= 0 || x < 0 || y < 0 || x + w > keyboard_width + 1
                || y + h > keyboard_height + 1) {
            return 0;
        }
        xs.push_back(x);
        ys.push_back(y);
        ws.push_back(w);
        hs.push_back(h);
        heights.push_back(h);
        codes.push_back(keys[i].codepoint);
    }
    /* Tap-time neighbour grid, unused by geometric input but validated by
     * length in the constructor: it must be grid-sized, not key-sized. */
    HostArray proximityChars;
    proximityChars.ints.assign(kGridWidth * kGridHeight * MAX_PROXIMITY_CHARS_SIZE,
            NOT_A_CODE_POINT);
    HostArray ax, ay, aw, ah, ac;
    ax.ints = xs;
    ay.ints = ys;
    aw.ints = ws;
    ah.ints = hs;
    ac.ints = codes;
    JNIEnv *env = makeHostJniEnv();
    /* Null sweet spots match an uncalibrated layout (as recorded on device):
     * key centres are then used unchanged, and HAS_TOUCH_POSITION_CORRECTION_DATA
     * stays off so calibration cannot drag centres to the origin. */
    std::unique_ptr<ProximityInfo> proximity(new (std::nothrow) ProximityInfo(env,
            keyboard_width, keyboard_height, kGridWidth, kGridHeight, most_common_key_width,
            modalOf(heights, most_common_key_width), reinterpret_cast<jintArray>(&proximityChars),
            n_keys, reinterpret_cast<jintArray>(&ax), reinterpret_cast<jintArray>(&ay),
            reinterpret_cast<jintArray>(&aw), reinterpret_cast<jintArray>(&ah),
            reinterpret_cast<jintArray>(&ac), nullptr, nullptr, nullptr));
    if (!proximity) {
        return 0;
    }
    glide->proximity = std::move(proximity);
    glide->keyboardWidth = keyboard_width;
    glide->keyboardHeight = keyboard_height;
    return 1;
}

int xxglide_decode(XxGlide *glide, const float *xs, const float *ys, const int *times_ms,
        int n_points, const char *prev_word, char ***out_words, int max_words) {
    if (out_words) {
        *out_words = nullptr;
    }
    if (!glide || !glide->dict || !glide->proximity || !xs || !ys || !times_ms || !out_words
            || n_points <= 0 || max_words <= 0) {
        return 0;
    }
    std::vector<int> xPoints(n_points), yPoints(n_points), times(n_points),
            pointerIds(n_points, 0), inputCodes(n_points, NOT_A_CODE_POINT);
    for (int i = 0; i < n_points; ++i) {
        if (!std::isfinite(xs[i]) || !std::isfinite(ys[i])) {
            return 0;
        }
        xPoints[i] = static_cast<int>(std::lround(xs[i]));
        yPoints[i] = static_cast<int>(std::lround(ys[i]));
        times[i] = times_ms[i];
    }

    /* isGesture only; the rest (full edit distance, offensive blocking, space
     * awareness) stay off for a plain glide decode. */
    const int options[] = {1 /* isGesture */, 0, 0, 0};
    SuggestOptions suggestOptions(options, 4);

    NgramContext emptyContext;
    const NgramContext *ngramContext = &emptyContext;
    std::vector<int> prevCodes;
    std::unique_ptr<NgramContext> prevContext;
    if (prev_word && *prev_word) {
        prevCodes = decodeUtf8Lower(prev_word);
        if (!prevCodes.empty()) {
            prevContext.reset(new NgramContext(prevCodes.data(),
                    static_cast<int>(prevCodes.size()), false /* isBeginningOfSentence */));
            ngramContext = prevContext.get();
        }
    }

    JNIEnv *env = makeHostJniEnv();
    DicTraverseSession traverseSession(env, nullptr /* localeStr */, true /* usesLargeCache */);
    traverseSession.init(glide->dict, ngramContext, &suggestOptions);

    SuggestionResults results(MAX_RESULTS);
    glide->dict->getSuggestions(glide->proximity.get(), &traverseSession, xPoints.data(),
            yPoints.data(), times.data(), pointerIds.data(), inputCodes.data(), n_points,
            ngramContext, &suggestOptions, 1.0f /* weightOfLangModelVsSpatialModel */, &results);

    std::vector<SuggestedWord> sorted;
    results.copySortedSuggestions(&sorted);
    const int n = std::min<int>(std::min<int>(sorted.size(), max_words), MAX_RESULTS);
    if (n <= 0) {
        return 0;
    }
    char **words = static_cast<char **>(calloc(n, sizeof(char *)));
    if (!words) {
        return 0;
    }
    int written = 0;
    for (int i = 0; i < n; ++i) {
        const std::string text = encodeUtf8(sorted[i].getCodePoint(), sorted[i].getCodePointCount());
        if (text.empty()) {
            continue;
        }
        words[written] = static_cast<char *>(malloc(text.size() + 1));
        if (!words[written]) {
            break;
        }
        memcpy(words[written], text.c_str(), text.size() + 1);
        ++written;
    }
    if (written == 0) {
        free(words);
        return 0;
    }
    *out_words = words;
    return written;
}

void xxglide_free_words(char **words, int n_words) {
    if (!words) {
        return;
    }
    for (int i = 0; i < n_words; ++i) {
        free(words[i]);
    }
    free(words);
}
