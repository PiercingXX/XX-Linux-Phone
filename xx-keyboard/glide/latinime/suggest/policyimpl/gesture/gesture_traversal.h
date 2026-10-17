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

#ifndef LATINIME_GESTURE_TRAVERSAL_H
#define LATINIME_GESTURE_TRAVERSAL_H

#include <cstdint>

#include "defines.h"
#include "suggest/core/dicnode/dic_node.h"
#include "suggest/core/dicnode/dic_node_vector.h"
#include "suggest/core/layout/proximity_info.h"
#include "suggest/core/layout/proximity_info_state.h"
#include "suggest/core/policy/traversal.h"
#include "suggest/core/session/dic_traverse_session.h"
#include "suggest/core/suggest_options.h"
#include "suggest/policyimpl/gesture/gesture_scoring_params.h"
#include "utils/char_utils.h"

namespace latinime {

// Traversal policy for gesture (glide) input.
//
// Edit corrections are all disabled here. When tapping, an omission or a
// transposition is a plausible slip with a bounded cost. When gliding, the path
// is continuous and every letter of the intended word lies somewhere on it, so
// there is nothing to omit or transpose — allowing those edits only lets the
// search invent words that the path does not support, and it multiplies the
// branching factor over an input that is already several times longer than the
// word. Letters the user swept past are handled by the align-point scan in
// GestureWeighting::getMatchedCost() instead.
class GestureTraversal : public Traversal {
 public:
    static const GestureTraversal *getInstance() { return &sInstance; }

    AK_FORCE_INLINE int getMaxPointerCount() const {
        return MAX_POINTER_COUNT_G;
    }

    AK_FORCE_INLINE bool allowsErrorCorrections(const DicNode *const dicNode) const {
        return false;
    }

    AK_FORCE_INLINE bool isOmission(const DicTraverseSession *const traverseSession,
            const DicNode *const dicNode, const DicNode *const childDicNode,
            const bool allowsErrorCorrections) const {
        return false;
    }

    AK_FORCE_INLINE bool isSpaceSubstitutionTerminal(
            const DicTraverseSession *const traverseSession, const DicNode *const dicNode) const {
        return false;
    }

    AK_FORCE_INLINE bool isSpaceOmissionTerminal(
            const DicTraverseSession *const traverseSession, const DicNode *const dicNode) const {
        // Multi-word gestures (gliding through a whole phrase without lifting) are
        // not decoded yet; one gesture produces one word.
        return false;
    }

    AK_FORCE_INLINE bool shouldDepthLevelCache(
            const DicTraverseSession *const traverseSession) const {
        return false;
    }

    AK_FORCE_INLINE bool shouldNodeLevelCache(
            const DicTraverseSession *const traverseSession, const DicNode *const dicNode) const {
        return false;
    }

    AK_FORCE_INLINE bool canDoLookAheadCorrection(
            const DicTraverseSession *const traverseSession, const DicNode *const dicNode) const {
        return false;
    }

    AK_FORCE_INLINE ProximityType getProximityType(
            const DicTraverseSession *const traverseSession, const DicNode *const dicNode,
            const DicNode *const childDicNode) const {
        // Prune on reachability rather than on adjacency. updateSampledSearchKeySets()
        // recorded which keys remain reachable from each point onward; if this letter
        // is not among them the path has already moved past it and no descendant can
        // recover, so the whole subtree is dead.
        const ProximityInfoState *const pInfoState = traverseSession->getProximityInfoState(0);
        const int inputIndex = dicNode->getInputIndex(0);
        if (inputIndex >= traverseSession->getInputSize()) {
            return UNRELATED_CHAR;
        }
        const int keyId = traverseSession->getProximityInfo()->getKeyIndexOf(
                CharUtils::toBaseLowerCase(childDicNode->getNodeCodePoint()));
        if (keyId == NOT_AN_INDEX) {
            return UNRELATED_CHAR;
        }
        if (!pInfoState->isKeyInSerchKeysAfterIndex(inputIndex, keyId)) {
            return UNRELATED_CHAR;
        }
        return MATCH_CHAR;
    }

    AK_FORCE_INLINE bool needsToTraverseAllUserInput() const {
        // The path has far more points than the word has letters, and the last letter
        // is not required to land on the final sampled point (the finger overshoots
        // and decelerates). Terminal cost, not a hard requirement, accounts for
        // leftover path — see GestureWeighting::getTerminalSpatialCost().
        return false;
    }

    AK_FORCE_INLINE float getMaxSpatialDistance() const {
        return GestureScoringParams::MAX_POINT_TO_KEY_LENGTH;
    }

    AK_FORCE_INLINE int getDefaultExpandDicNodeSize() const {
        return DicNodeVector::DEFAULT_NODES_SIZE_FOR_OPTIMIZATION;
    }

    AK_FORCE_INLINE int getMaxCacheSize(const int inputSize, const float weightForLocale) const {
        if (inputSize <= 1) {
            return GestureScoringParams::MAX_CACHE_DIC_NODE_SIZE_FOR_SINGLE_POINT;
        }
        return GestureScoringParams::MAX_CACHE_DIC_NODE_SIZE;
    }

    AK_FORCE_INLINE int getTerminalCacheSize() const {
        return MAX_RESULTS;
    }

    AK_FORCE_INLINE bool isPossibleOmissionChildNode(
            const DicTraverseSession *const traverseSession, const DicNode *const parentDicNode,
            const DicNode *const dicNode) const {
        return false;
    }

    AK_FORCE_INLINE bool isGoodToTraverseNextWord(const DicNode *const dicNode,
            const int probability) const {
        return false;
    }

 private:
    DISALLOW_COPY_AND_ASSIGN(GestureTraversal);
    static const GestureTraversal sInstance;

    GestureTraversal() {}
    ~GestureTraversal() {}
};
} // namespace latinime
#endif // LATINIME_GESTURE_TRAVERSAL_H
