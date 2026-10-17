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

#ifndef LATINIME_GESTURE_WEIGHTING_H
#define LATINIME_GESTURE_WEIGHTING_H

#include <algorithm>

#include "defines.h"
#include "suggest/core/dicnode/dic_node.h"
#include "suggest/core/dicnode/dic_node_utils.h"
#include "suggest/core/dictionary/error_type_utils.h"
#include "suggest/core/layout/proximity_info.h"
#include "suggest/core/layout/proximity_info_state.h"
#include "suggest/core/policy/weighting.h"
#include "suggest/core/session/dic_traverse_session.h"
#include "suggest/policyimpl/gesture/gesture_scoring_params.h"
#include "utils/char_utils.h"

namespace latinime {

class DicNode;
struct DicNode_InputStateG;
class MultiBigramMap;

// Spatial weighting for gesture (glide) input.
//
// The difference from TypingWeighting is what "one input" means. When tapping,
// input index i is the i-th keypress and a letter either matches it or it does
// not. When gliding, the input is a resampled path of many more points than the
// word has letters, and the decoder has to decide *which* point along the path
// each letter was aimed at. ProximityInfoState has already done the geometry for
// us: updateAlignPointProbabilities() fills a per-point map of key -> cost, which
// getProbability() reads back (despite the name it returns a cost, and
// MAX_VALUE_FOR_WEIGHTING when the key is not a candidate at that point).
//
// So getMatchedCost() below is the heart of the decoder: given a letter and the
// point the parent node stopped at, scan forward for the cheapest align point,
// charge for the points skipped along the way, and report the chosen point back
// through DicNode_InputStateG so the child node resumes from there.
class GestureWeighting : public Weighting {
 public:
    static const GestureWeighting *getInstance() { return &sInstance; }

 protected:
    float getTerminalSpatialCost(const DicTraverseSession *const traverseSession,
            const DicNode *const dicNode) const {
        // Charge for path the word never accounted for, per unconsumed point.
        //
        // This has to stay on the same scale as a per-letter match cost (~0.2 to
        // ~2.3), because it is the only thing opposing early termination: every
        // extra letter adds cost, so without a comparable charge for leftover path
        // the search always takes the shortest word covering the start of the
        // gesture. Dividing by inputSize (as this originally did) capped the whole
        // penalty at one constant and produced exactly that — "What" decoding to
        // "Way", "actual" to a three-letter fragment.
        const int inputIndex = dicNode->getInputIndex(0);
        const int inputSize = traverseSession->getInputSize();
        const int unconsumed = inputSize - inputIndex;
        if (unconsumed <= 0) {
            return 0.0f;
        }
        return GestureScoringParams::TERMINAL_INSERTION_COST * static_cast<float>(unconsumed);
    }

    // Gesture does not model omission/insertion/transposition edits: the traversal
    // disables them, so these are unreachable. They must still be defined because
    // Weighting declares them pure virtual.
    float getOmissionCost(const DicNode *const parentDicNode, const DicNode *const dicNode) const {
        return static_cast<float>(MAX_VALUE_FOR_WEIGHTING);
    }

    float getMatchedCost(const DicTraverseSession *const traverseSession,
            const DicNode *const dicNode, DicNode_InputStateG *inputStateG) const {
        const ProximityInfoState *const pInfoState = traverseSession->getProximityInfoState(0);
        const int inputSize = traverseSession->getInputSize();
        const int startIndex = dicNode->getInputIndex(0);
        const int codePoint = dicNode->getNodeCodePoint();
        const int baseLowerCodePoint = CharUtils::toBaseLowerCase(codePoint);
        const int keyId = traverseSession->getProximityInfo()->getKeyIndexOf(baseLowerCodePoint);
        if (keyId == NOT_AN_INDEX || startIndex >= inputSize) {
            return static_cast<float>(MAX_VALUE_FOR_WEIGHTING);
        }

        // A repeated letter is spatially invisible in a glide: the finger is at the
        // same key for both. Charge according to how much the finger slowed down
        // there — a cusp or loop (low beeline speed) is real evidence the user meant
        // a double letter — but never reject outright. Scanning forward for a second
        // align point instead finds nothing: suppressCharProbabilities() has already
        // stripped this key from every point but the one the first letter took, so
        // on replay "really", "little" and "different" were unreachable.
        if (baseLowerCodePoint == CharUtils::toBaseLowerCase(inputStateG->mPrevCodePoint)
                || (startIndex > 0 && baseLowerCodePoint
                        == CharUtils::toBaseLowerCase(dicNode->getPrevCodePointG(0)))) {
            const DoubleLetterLevel level = pInfoState->getDoubleLetterLevel(startIndex);
            const bool slowed = level == A_STRONG_DOUBLE_LETTER || level == A_DOUBLE_LETTER;
            // A slowed repeat consumes the point it slowed at. An unslowed one
            // stays put: there is no second point for it, and the trie already
            // bounds how many times a letter can repeat.
            inputStateG->mNeedsToUpdateInputStateG = true;
            inputStateG->mPointerId = 0;
            inputStateG->mInputIndex = static_cast<int16_t>(startIndex + (slowed ? 1 : 0));
            inputStateG->mPrevCodePoint = codePoint;
            inputStateG->mTerminalDiffCost = 0.0f;
            inputStateG->mRawLength = 0.0f;
            inputStateG->mDoubleLetterLevel = level;
            if (level == A_STRONG_DOUBLE_LETTER) {
                return GestureScoringParams::STRONG_DOUBLE_LETTER_COST;
            }
            return slowed ? GestureScoringParams::DOUBLE_LETTER_COST
                    : GestureScoringParams::UNSLOWED_DOUBLE_LETTER_COST;
        }

        // Scan forward for the cheapest point at which this key was plausibly aimed
        // at. updateSampledSearchKeySets() precomputed, for each point, which keys can
        // still be reached from there on; once this key drops out of that set the path
        // has moved past it for good and there is no reason to keep looking.
        //
        // Points between the previous letter and this one are path the word does not
        // explain. Some skipping is normal (the finger travels between keys), so this
        // is a gentle per-point charge rather than a hard limit. The first letter is
        // anchored to the start of the path: the finger began on the first key, so
        // points skipped before it are strong evidence against the word rather than
        // ordinary travel between keys.
        //
        // The align point is the one minimising distance plus skip cost together.
        // Taking the nearest point and charging skips afterwards let one close
        // pass late in the path outbid the corner the finger actually turned at.
        const bool isFirstLetter = dicNode->getNodeCodePointCount() == 1;
        const float alignWeight = GestureScoringParams::DISTANCE_WEIGHT_ALIGN_POINT;
        const float perPointSkip = isFirstLetter ? GestureScoringParams::FIRST_LETTER_SKIP_COST
                : GestureScoringParams::SKIPPED_POINT_COST;
        float minCost = static_cast<float>(MAX_VALUE_FOR_WEIGHTING);
        float minTotal = static_cast<float>(MAX_VALUE_FOR_WEIGHTING);
        float skipCost = 0.0f; // for the points before bestIndex
        int bestIndex = NOT_AN_INDEX;
        float runningSkip = 0.0f; // for the points before i
        const int lastIndex = std::min(inputSize - 1,
                startIndex + GestureScoringParams::MAX_ALIGN_POINT_LOOKAHEAD);
        for (int i = startIndex; i <= lastIndex; ++i) {
            const float cost = pInfoState->getProbability(i, keyId);
            if (cost < static_cast<float>(MAX_VALUE_FOR_WEIGHTING)) {
                const float total = alignWeight * cost + runningSkip;
                if (total < minTotal) {
                    minCost = cost;
                    minTotal = total;
                    skipCost = runningSkip;
                    bestIndex = i;
                }
            }
            if (!pInfoState->isKeyInSerchKeysAfterIndex(i, keyId)) {
                break;
            }
            runningSkip += perPointSkip;
            if (!isFirstLetter && GestureScoringParams::SKIP_PROBABILITY_WEIGHT > 0.0f) {
                runningSkip += GestureScoringParams::SKIP_PROBABILITY_WEIGHT * std::min(
                        pInfoState->getProbability(i, NOT_AN_INDEX), MAX_POINT_SKIP_COST);
            }
        }
        if (bestIndex == NOT_AN_INDEX || minCost >= static_cast<float>(MAX_VALUE_FOR_WEIGHTING)) {
            return static_cast<float>(MAX_VALUE_FOR_WEIGHTING);
        }

        // A letter the finger merely passed over on the way somewhere else is weaker
        // evidence than one it aimed at. The align-point machinery marks deliberate
        // hits as local minima in point-to-key distance.
        const float passedOverCost = isLocalMin(pInfoState, bestIndex, keyId, inputSize)
                ? 0.0f : GestureScoringParams::PASSED_OVER_COST;

        const float rawLength = static_cast<float>(
                pInfoState->getLengthCache(bestIndex) - pInfoState->getLengthCache(startIndex));

        inputStateG->mNeedsToUpdateInputStateG = true;
        inputStateG->mPointerId = 0;
        inputStateG->mInputIndex = static_cast<int16_t>(bestIndex + 1);
        inputStateG->mPrevCodePoint = codePoint;
        inputStateG->mTerminalDiffCost = 0.0f;
        inputStateG->mRawLength = rawLength;
        inputStateG->mDoubleLetterLevel = NOT_A_DOUBLE_LETTER;

        return alignWeight * minCost + skipCost + passedOverCost;
    }

    bool isProximityDicNode(const DicTraverseSession *const traverseSession,
            const DicNode *const dicNode) const {
        // Every gesture match is approximate; there is no "typed the exact key"
        // notion to compare against, so nothing is flagged as a proximity error.
        return false;
    }

    float getTranspositionCost(const DicTraverseSession *const traverseSession,
            const DicNode *const parentDicNode, const DicNode *const dicNode) const {
        return static_cast<float>(MAX_VALUE_FOR_WEIGHTING);
    }

    float getInsertionCost(const DicTraverseSession *const traverseSession,
            const DicNode *const parentDicNode, const DicNode *const dicNode) const {
        return static_cast<float>(MAX_VALUE_FOR_WEIGHTING);
    }

    float getSpaceOmissionCost(const DicTraverseSession *const traverseSession,
            const DicNode *const dicNode, DicNode_InputStateG *inputStateG) const {
        return static_cast<float>(MAX_VALUE_FOR_WEIGHTING);
    }

    float getNewWordBigramLanguageCost(const DicTraverseSession *const traverseSession,
            const DicNode *const dicNode, MultiBigramMap *const multiBigramMap) const {
        return DicNodeUtils::getBigramNodeImprobability(
                traverseSession->getDictionaryStructurePolicy(), dicNode, multiBigramMap)
                * GestureScoringParams::DISTANCE_WEIGHT_LANGUAGE;
    }

    float getCompletionCost(const DicTraverseSession *const traverseSession,
            const DicNode *const dicNode) const {
        if (isRepeatAtEndOfPath(dicNode)) {
            // Priced like a double letter the finger did not slow for.
            return GestureScoringParams::UNSLOWED_DOUBLE_LETTER_COST;
        }
        const bool firstCompletion =
                dicNode->getInputIndex(0) == traverseSession->getInputSize();
        return firstCompletion ? GestureScoringParams::COST_FIRST_COMPLETION
                : GestureScoringParams::COST_COMPLETION;
    }

    float getTerminalLanguageCost(const DicTraverseSession *const traverseSession,
            const DicNode *const dicNode, const float dicNodeLanguageImprobability) const {
        return dicNodeLanguageImprobability * GestureScoringParams::DISTANCE_WEIGHT_LANGUAGE
                + (isAllCaps(dicNode) ? GestureScoringParams::ALL_CAPS_COST : 0.0f);
    }

    float getTerminalInsertionCost(const DicTraverseSession *const traverseSession,
            const DicNode *const dicNode) const {
        return static_cast<float>(MAX_VALUE_FOR_WEIGHTING);
    }

    AK_FORCE_INLINE bool needsToNormalizeCompoundDistance() const {
        // Gesture words are scored against a path whose point count is unrelated to
        // the word length, so distances must be normalized before words of different
        // lengths can be compared.
        return true;
    }

    AK_FORCE_INLINE float getAdditionalProximityCost() const {
        return static_cast<float>(MAX_VALUE_FOR_WEIGHTING);
    }

    AK_FORCE_INLINE float getSubstitutionCost() const {
        return static_cast<float>(MAX_VALUE_FOR_WEIGHTING);
    }

    AK_FORCE_INLINE float getSpaceSubstitutionCost(const DicTraverseSession *const traverseSession,
            const DicNode *const dicNode) const {
        return static_cast<float>(MAX_VALUE_FOR_WEIGHTING);
    }

    ErrorTypeUtils::ErrorType getErrorType(const CorrectionType correctionType,
            const DicTraverseSession *const traverseSession,
            const DicNode *const parentDicNode, const DicNode *const dicNode) const;

 private:
    DISALLOW_COPY_AND_ASSIGN(GestureWeighting);
    static const GestureWeighting sInstance;

    // -log(skip probability) is unbounded where the geometry all but ruled a skip
    // out; cap it so one such point prices like a strong mismatch, not a reject.
    static constexpr float MAX_POINT_SKIP_COST = 5.0f;

    GestureWeighting() {}
    ~GestureWeighting() {}

    // Two or more letters, every one of them upper case.
    static bool isAllCaps(const DicNode *const dicNode) {
        const int count = dicNode->getNodeCodePointCount();
        if (count < 2) {
            return false;
        }
        const int *const word = dicNode->getOutputWordBuf();
        for (int i = 0; i < count; ++i) {
            if (!CharUtils::isAsciiUpper(word[i])) {
                return false;
            }
        }
        return true;
    }

    // True for a letter past the end of the path that repeats the letter which took
    // the path's last point, i.e. the second half of a word-final double letter.
    static bool isRepeatAtEndOfPath(const DicNode *const dicNode) {
        return dicNode->getNodeCodePointCount() > 1
                && !ErrorTypeUtils::isCompletion(dicNode->getContainedErrorTypes())
                && CharUtils::toBaseLowerCase(dicNode->getNodeCodePoint())
                        == CharUtils::toBaseLowerCase(dicNode->getPrevCodePointG(0));
    }

    // True when the path is closer to this key at `index` than at the neighbouring
    // points, i.e. the finger turned or lingered here rather than sweeping through.
    static bool isLocalMin(const ProximityInfoState *const pInfoState, const int index,
            const int keyId, const int inputSize) {
        const float here = pInfoState->getPointToKeyByIdLength(index, keyId);
        if (index > 0 && pInfoState->getPointToKeyByIdLength(index - 1, keyId) < here) {
            return false;
        }
        if (index + 1 < inputSize
                && pInfoState->getPointToKeyByIdLength(index + 1, keyId) < here) {
            return false;
        }
        return true;
    }
};
} // namespace latinime
#endif // LATINIME_GESTURE_WEIGHTING_H
