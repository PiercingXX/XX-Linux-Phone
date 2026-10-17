/*
 * Copyright (C) 2026 PiercingXX
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "suggest/policyimpl/gesture/gesture_scoring_params.h"

#ifdef HOST_TOOL
#include <cstdlib>
#endif

namespace latinime {

// On the host these constants can be overridden by an environment variable of
// the same name, so tools/glide-replay can sweep them without a rebuild. The
// device build is unaffected: PARAM collapses to the literal.
#ifdef HOST_TOOL
static float paramOrDefault(const char *const name, const float defaultValue) {
    const char *const value = getenv(name);
    return value != nullptr ? static_cast<float>(atof(value)) : defaultValue;
}
#define PARAM(name, value) paramOrDefault(#name, value)
#else
#define PARAM(name, value) value
#endif

// Compiled defaults are the source of truth. Must stay in lockstep with
// tools/glide-replay/sweep.py BASELINE (HOST_TOOL env overrides do not count).
//
// Tuned by sweep.py on 2000 human swipes from the FUTO swipe dataset (QWERTY,
// MIT; see tools/glide-replay/futo_convert.py) plus 121 labelled tester glides
// on Colemak (weighted 4x), and checked on 3000 held-out FUTO swipes.
// 2026-09-24: top-1 71.2% -> 80.5%, top-4 81.5% -> 88.7%.
// 2026-09-26, geometry skip cost + joint align point: top-1 -> 83.5%, top-4 ->
// 90.9%. Then word-final doubles and ALL_CAPS_COST, re-swept with replay using
// the device's key pitch: top-1 84.4%, top-4 92.0%; tester glides 85 of 121
// (83 before this pass, same geometry), piercing corpus 16 of 19.
// 2026-09-30, coordinate descent on 879 labelled Colemak glides from this phone
// (kept / picked / replaced / deleted): top-1 612/879 -> 617/879, top-4 held
// at 747/879.
const float GestureScoringParams::DISTANCE_WEIGHT_ALIGN_POINT = PARAM(DISTANCE_WEIGHT_ALIGN_POINT, 0.25f);
// Was 0.03 behind an int truncation to 0, then 1.0, then 0.05. On the
// 2026-09-30 Colemak corpus a wider cap (0.27) keeps keys the finger only grazed.
const float GestureScoringParams::MAX_POINT_TO_KEY_LENGTH = PARAM(MAX_POINT_TO_KEY_LENGTH, 0.27f);
// A flat per-point charge now only breaks ties; SKIP_PROBABILITY_WEIGHT carries
// the real signal (cheap on straight fast travel, dear at corners). At a flat
// 0.35 a long straight run cost more than any wrong word, so "all" off a-s-d-
// f-g-h-j-k-l decoded as "ago".
const float GestureScoringParams::SKIPPED_POINT_COST = PARAM(SKIPPED_POINT_COST, 0.035f);
const float GestureScoringParams::SKIP_PROBABILITY_WEIGHT = PARAM(SKIP_PROBABILITY_WEIGHT, 0.3f);
// Near zero: a glide sweeps through most letters rather than cornering on them,
// so "never a local minimum" is weak evidence against a letter. On the piercing
// home row (A S E T D H N U I O) a large charge dropped vowels and emitted
// consonant skeletons ("SMG", "FCK").
const float GestureScoringParams::PASSED_OVER_COST = PARAM(PASSED_OVER_COST, 0.0f);
// A word must begin where the finger began. Without a heavy charge here, a
// shorter word simply ignores the front of the path ("he" off a t-h-e gesture)
// and wins by having fewer letters to pay for.
const float GestureScoringParams::FIRST_LETTER_SKIP_COST = PARAM(FIRST_LETTER_SKIP_COST, 1.6f);

// Well below a typical match cost. At 0.55/0.95 every double-letter word on
// device lost its double: been->Ben, coffee->cot, really->ray, little->lie.
// The language model decides between "ll" and "l"; the path cannot.
const float GestureScoringParams::DOUBLE_LETTER_COST = PARAM(DOUBLE_LETTER_COST, 0.0f);
const float GestureScoringParams::STRONG_DOUBLE_LETTER_COST = PARAM(STRONG_DOUBLE_LETTER_COST, 0.10f);
const float GestureScoringParams::UNSLOWED_DOUBLE_LETTER_COST = PARAM(UNSLOWED_DOUBLE_LETTER_COST, 0.05f);

const float GestureScoringParams::DISTANCE_WEIGHT_LANGUAGE = PARAM(DISTANCE_WEIGHT_LANGUAGE, 3.5f);
const float GestureScoringParams::COST_COMPLETION = PARAM(COST_COMPLETION, 1.0f);
const float GestureScoringParams::COST_FIRST_COMPLETION = PARAM(COST_FIRST_COMPLETION, 0.25f);
const float GestureScoringParams::TERMINAL_INSERTION_COST = PARAM(TERMINAL_INSERTION_COST, 0.5f);
const float GestureScoringParams::ALL_CAPS_COST = PARAM(ALL_CAPS_COST, 1.0f);

const float GestureScoringParams::BASE_OUTPUT_SCORE = PARAM(BASE_OUTPUT_SCORE, 1.0f);
// Measured on device: compound distances for real words run ~1.5 to ~20 over a
// 7-point path, so the divisor has to be on that scale or every dictionary word
// scores negative and loses to the fallback below.
const float GestureScoringParams::MAX_OUTPUT_SCORE_PER_INPUT = PARAM(MAX_OUTPUT_SCORE_PER_INPUT, 1.2f);
// Pushed below zero deliberately. The raw path reading favours the corners of
// the gesture, which are consonants, so on device it emitted skeletons like
// "SMG"/"FCK"/"WD" that outranked correct words. It should lose to any
// dictionary hit that scores at all, and surface only when there are none.
const float GestureScoringParams::MOST_PROBABLE_STRING_DEMOTION = PARAM(MOST_PROBABLE_STRING_DEMOTION, 1.6f);

const int GestureScoringParams::MAX_CACHE_DIC_NODE_SIZE = static_cast<int>(PARAM(MAX_CACHE_DIC_NODE_SIZE, 170.0f));
const int GestureScoringParams::MAX_CACHE_DIC_NODE_SIZE_FOR_SINGLE_POINT = static_cast<int>(PARAM(MAX_CACHE_DIC_NODE_SIZE_FOR_SINGLE_POINT, 310.0f));
const int GestureScoringParams::MAX_ALIGN_POINT_LOOKAHEAD = static_cast<int>(PARAM(MAX_ALIGN_POINT_LOOKAHEAD, 18.0f));
} // namespace latinime
