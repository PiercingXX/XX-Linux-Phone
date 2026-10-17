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

#include "suggest/policyimpl/gesture/gesture_weighting.h"

#include "suggest/core/dicnode/dic_node.h"
#include "suggest/core/layout/proximity_info.h"

namespace latinime {

const GestureWeighting GestureWeighting::sInstance;

ErrorTypeUtils::ErrorType GestureWeighting::getErrorType(const CorrectionType correctionType,
        const DicTraverseSession *const traverseSession, const DicNode *const parentDicNode,
        const DicNode *const dicNode) const {
    // A gesture never "matches exactly" the way a tap does — the user drew through a
    // region, not onto a key — so matches are reported as clean rather than as
    // corrections. Marking them as errors would suppress them via the exact-match
    // and offensive-word paths in SuggestionsOutputUtils.
    switch (correctionType) {
        case CT_MATCH:
            return ErrorTypeUtils::NOT_AN_ERROR;
        case CT_COMPLETION:
            // The second letter of a word-final double ("all", "see") has no point
            // left to take once the first one claimed the end of the path. It is
            // not a completion: calling it one ranks the word below every exact
            // match regardless of cost (DicNode::compare), and "all" lost to "SL".
            return isRepeatAtEndOfPath(dicNode) ? ErrorTypeUtils::NOT_AN_ERROR
                    : ErrorTypeUtils::COMPLETION;
        case CT_TERMINAL:
            return ErrorTypeUtils::NOT_AN_ERROR;
        case CT_NEW_WORD_SPACE_OMISSION:
        case CT_NEW_WORD_SPACE_SUBSTITUTION:
            return ErrorTypeUtils::NEW_WORD;
        default:
            return ErrorTypeUtils::EDIT_CORRECTION;
    }
}
}  // namespace latinime
