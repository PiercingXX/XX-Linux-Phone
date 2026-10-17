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

#ifndef LATINIME_GESTURE_SCORING_H
#define LATINIME_GESTURE_SCORING_H

#include <algorithm>

#include "defines.h"
#include "suggest/core/dictionary/dictionary.h"
#include "suggest/core/dictionary/error_type_utils.h"
#include "suggest/core/layout/proximity_info_state.h"
#include "suggest/core/policy/scoring.h"
#include "suggest/core/result/suggestion_results.h"
#include "suggest/core/session/dic_traverse_session.h"
#include "suggest/policyimpl/gesture/gesture_scoring_params.h"

namespace latinime {

class DicNode;
class DicTraverseSession;

class GestureScoring : public Scoring {
 public:
    static const GestureScoring *getInstance() { return &sInstance; }

    // Emit the raw path reading — the single most likely letter at each align point,
    // with no dictionary consulted — as an extra candidate. Names, slang and typos
    // are not in the dictionary and would otherwise be undecodable by gesture at all.
    // It is demoted below every traversal result so it only surfaces when the
    // dictionary had nothing to offer.
    AK_FORCE_INLINE void getMostProbableString(const DicTraverseSession *const traverseSession,
            const float weightOfLangModelVsSpatialModel,
            SuggestionResults *const outSuggestionResults) const {
        const ProximityInfoState *const pInfoState = traverseSession->getProximityInfoState(0);
        if (!pInfoState->isUsed() || pInfoState->size() <= 0) {
            return;
        }
        int codePoints[MAX_WORD_LENGTH];
        const float probability = pInfoState->getMostProbableString(codePoints);
        if (probability >= static_cast<float>(MAX_VALUE_FOR_WEIGHTING)) {
            return;
        }
        int codePointCount = 0;
        while (codePointCount < MAX_WORD_LENGTH && codePoints[codePointCount] != 0) {
            ++codePointCount;
        }
        if (codePointCount <= 0) {
            return;
        }
        const float score = GestureScoringParams::BASE_OUTPUT_SCORE
                - GestureScoringParams::MOST_PROBABLE_STRING_DEMOTION;
        outSuggestionResults->addSuggestion(codePoints, codePointCount,
                static_cast<int>(score * SUGGEST_INTERFACE_OUTPUT_SCALE),
                Dictionary::KIND_CORRECTION, NOT_AN_INDEX,
                NOT_A_FIRST_WORD_CONFIDENCE);
    }

    AK_FORCE_INLINE float getAdjustedWeightOfLangModelVsSpatialModel(
            DicTraverseSession *const traverseSession, DicNode *const terminals,
            const int size) const {
        return 1.0f;
    }

    AK_FORCE_INLINE int calculateFinalScore(const float compoundDistance, const int inputSize,
            const ErrorTypeUtils::ErrorType containedErrorTypes, const bool forceCommit,
            const bool boostExactMatches, const bool hasProbabilityZero) const {
        // Longer paths accumulate more distance across more matched letters, so the
        // divisor scales with input size exactly as the typing policy's does.
        const float maxDistance = GestureScoringParams::DISTANCE_WEIGHT_LANGUAGE
                + static_cast<float>(inputSize)
                        * GestureScoringParams::MAX_OUTPUT_SCORE_PER_INPUT;
        const float score =
                GestureScoringParams::BASE_OUTPUT_SCORE - compoundDistance / maxDistance;
        // Nodes the weighting rejected carry MAX_VALUE_FOR_WEIGHTING and can still
        // reach here as terminals. Casting their score to int overflows to INT_MIN,
        // which is undefined behaviour, so clamp into a sane range first.
        const float clamped = std::max(-1.0f, std::min(1.0f, score));
        return static_cast<int>(clamped * SUGGEST_INTERFACE_OUTPUT_SCALE);
    }

    AK_FORCE_INLINE float getDoubleLetterDemotionDistanceCost(
            const DicNode *const terminalDicNode) const {
        // Double letters are already priced in getMatchedCost() from how much the
        // finger slowed at the key, which is better evidence than anything available
        // at terminal time.
        return 0.0f;
    }

    AK_FORCE_INLINE bool autoCorrectsToMultiWordSuggestionIfTop() const {
        return false;
    }

    AK_FORCE_INLINE bool sameAsTyped(const DicTraverseSession *const traverseSession,
            const DicNode *const dicNode) const {
        // There is no "as typed" string for a gesture, so no candidate can be it.
        return false;
    }

 private:
    DISALLOW_COPY_AND_ASSIGN(GestureScoring);
    static const GestureScoring sInstance;

    GestureScoring() {}
    ~GestureScoring() {}
};
} // namespace latinime
#endif // LATINIME_GESTURE_SCORING_H
