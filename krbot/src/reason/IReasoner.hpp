#pragma once
// L4 reasoner seam. ClipsEngine (doc v3 §5.1, KRBOT_WITH_CLIPS, Phase 1) implements this with
// one CLIPS Environment* confined to the reasoning thread. NullReasoner is the stand-in.

#include "knowledge/Fact.hpp"
#include "reason/Goal.hpp"

#include <functional>

namespace krbot::reason {

struct ReasonerOutputs {
    std::function<void(Goal)> postGoal;                   // -> GoalQueue (doc v3 §5.4)
    std::function<void(knowledge::Fact)> assertDerived;   // -> FactStore, source=Inferred (§5.5)
};

class IReasoner {
public:
    virtual void syncDelta(const knowledge::FactDelta& d) = 0;
    // Bounded run (doc v3 §5.3): returns rules fired; hitting runLimit every tick = runaway rules.
    virtual long long tick(int runLimit) = 0;
    virtual ~IReasoner() = default;
};

}  // namespace krbot::reason
