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

#ifndef LATINIME_GESTURE_SCORING_PARAMS_H
#define LATINIME_GESTURE_SCORING_PARAMS_H

#include "defines.h"

namespace latinime {

// Tuning constants for the open gesture decoder. AOSP shipped a gutted gesture
// policy (the factory method was left null for the proprietary library to fill
// in), so unlike ScoringParams these were never numerically optimized upstream.
// They are hand-tuned starting points; see GestureWeighting for what each one
// multiplies.
class GestureScoringParams {
 public:
    // Spatial model.
    // Multiplies the align-point cost returned by ProximityInfoState::getProbability().
    static const float DISTANCE_WEIGHT_ALIGN_POINT;
    // Cap on the normalized squared point-to-key distance (1.0 = one key width,
    // squared). Traversal::getMaxSpatialDistance() hands this to ProximityInfoState
    // as maxPointToKeyLength; every key farther than the cap reads as exactly the
    // cap, so a small value flattens the whole spatial model.
    static const float MAX_POINT_TO_KEY_LENGTH;
    // Cost added per sampled point skipped between two matched letters. Keeps the
    // decoder from matching a word by cherry-picking far-apart align points.
    static const float SKIPPED_POINT_COST;
    // Weight on the geometry's own per-point skip cost (-log of the skip
    // probability ProximityInfoStateUtils assigns each point): near zero where the
    // finger was travelling fast and straight, large at corners and slow-downs.
    static const float SKIP_PROBABILITY_WEIGHT;
    // Cost for a letter that never becomes a local minimum along the path, i.e. a
    // letter the finger passed over in transit rather than deliberately hit.
    static const float PASSED_OVER_COST;
    // Charged per point skipped before the *first* letter, anchoring the word to
    // the start of the path. Much heavier than SKIPPED_POINT_COST.
    static const float FIRST_LETTER_SKIP_COST;

    // Double letters. A gesture cannot distinguish "ll" from "l" spatially, so
    // repeated letters are free when the finger visibly slowed (a cusp or loop) and
    // penalized when it did not.
    static const float DOUBLE_LETTER_COST;
    static const float STRONG_DOUBLE_LETTER_COST;
    // A repeated letter where the finger did not slow at all. Most people glide
    // straight through double letters, so this cannot be a hard reject.
    static const float UNSLOWED_DOUBLE_LETTER_COST;

    // Language model.
    static const float DISTANCE_WEIGHT_LANGUAGE;
    static const float COST_COMPLETION;
    static const float COST_FIRST_COMPLETION;
    // Charged per unconsumed sampled point when a word terminates early. This is
    // what stops "the" from beating "there" on a path drawn through all five keys.
    static const float TERMINAL_INSERTION_COST;
    // Charged once for a word spelled entirely in capitals ("SL", "ITU"). The
    // dictionary is full of short acronyms whose letters happen to lie on almost
    // any path, and people rarely glide them.
    static const float ALL_CAPS_COST;

    // Output scoring.
    static const float BASE_OUTPUT_SCORE;
    static const float MAX_OUTPUT_SCORE_PER_INPUT;
    // The raw most-probable-string (one letter per align point, no dictionary) is
    // offered as a fallback for out-of-vocabulary words. It must never outrank a
    // real dictionary hit, so it is scored below every traversal result.
    static const float MOST_PROBABLE_STRING_DEMOTION;

    // Search bounds.
    static const int MAX_CACHE_DIC_NODE_SIZE;
    static const int MAX_CACHE_DIC_NODE_SIZE_FOR_SINGLE_POINT;
    // How far past the current align point the decoder will look for the next
    // letter. Bounds the per-letter scan so long paths stay linear.
    static const int MAX_ALIGN_POINT_LOOKAHEAD;

 private:
    DISALLOW_IMPLICIT_CONSTRUCTORS(GestureScoringParams);
};
} // namespace latinime
#endif // LATINIME_GESTURE_SCORING_PARAMS_H
