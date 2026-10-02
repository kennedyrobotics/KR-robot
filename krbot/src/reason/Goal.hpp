#pragma once
// Goal - doc v3 §6, "deliberately thin". Posted by L4 (CLIPS `post-goal` UDF, §5.4) or parsed
// from operator commands (source=Operator); consumed by L5's GoalArbiter. Defined at L4 so the
// L4->L5 dependency points the same way as the data.

#include "common/ThreadSafeQueue.hpp"
#include "knowledge/Fact.hpp"

#include <string>

namespace krbot::reason {

struct Goal {
    std::string type;    // "Navigate" | "HoldPosition" | "Replan" | "ReturnToBase" | "Idle" ...
    std::string target;  // waypoint id, obstacle id, route id, ...
    int priority = 0;    // carried from the posting rule's salience (doc v3 §5.6 bands)
    knowledge::FactSource source = knowledge::FactSource::Inferred;
};

using GoalQueue = common::ThreadSafeQueue<Goal>;  // doc v3's g_goalQueue (owned by core, not global)

}  // namespace krbot::reason
