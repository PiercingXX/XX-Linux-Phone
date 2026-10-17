/*
 * Copyright (C) 2012 The Android Open Source Project
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

#include "gesture_suggest_policy_factory.h"

#include "suggest/policyimpl/gesture/gesture_suggest_policy.h"

namespace latinime {

// AOSP left this null so the proprietary libjni_latinimegoogle could install its
// decoder here at load time; with no such library present, gesture input silently
// decoded to nothing. It now defaults to the in-tree decoder, which is compiled
// into libjni_latinime itself, so glide typing needs no external library. The
// setter is kept, so a closed-source library still overrides this if one is loaded.
static const SuggestPolicy *getGestureSuggestPolicyInstance() {
    return GestureSuggestPolicy::getInstance();
}

const SuggestPolicy *(*GestureSuggestPolicyFactory::sGestureSuggestFactoryMethod)() =
        getGestureSuggestPolicyInstance;
} // namespace latinime
