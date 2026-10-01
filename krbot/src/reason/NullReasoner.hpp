#pragma once
// Stand-in L4 until CLIPS is vendored (Phase 1). Mirrors the working-memory bookkeeping ClipsEngine
// will do (liveFacts_ map, doc v3 §5.3) so the sync path is exercised end-to-end today; fires no rules.

#include "reason/IReasoner.hpp"

#include <unordered_map>

namespace krbot::reason {

class NullReasoner final : public IReasoner {
public:
    explicit NullReasoner(ReasonerOutputs out) : out_(std::move(out)) {}
    void syncDelta(const knowledge::FactDelta& d) override;
    long long tick(int runLimit) override;

    std::size_t workingMemorySize() const { return live_.size(); }
    std::size_t deltasSeen() const { return deltas_; }

private:
    ReasonerOutputs out_;
    std::unordered_map<knowledge::FactId, knowledge::Fact> live_;
    std::size_t deltas_ = 0;
};

}  // namespace krbot::reason
