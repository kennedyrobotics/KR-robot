#pragma once
// GoalArbiter — doc v3 §6. Each tick: drain the goal queue; if the best pending goal beats the
// active one by more than the hysteresis margin, halt the active executor and switch; otherwise
// tick the active executor. On Success/Failure, report a TaskOutcome fact (source=Execution) to L3
// and fall back to the next pending goal (or idle). Single-threaded: called from the L5 tick loop.

#include "exec/IBehaviorExecutor.hpp"
#include "knowledge/Fact.hpp"
#include "reason/Goal.hpp"

#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace krbot::exec {

class GoalArbiter {
public:
    using FactSink = std::function<void(knowledge::Fact)>;

    GoalArbiter(reason::GoalQueue& queue, ExecutorFactory factory, FactSink outcomes, int hysteresis = 10);

    void tick();

    std::optional<reason::Goal> activeGoal() const;
    std::string activeTaskId() const { return activeId_; }
    std::size_t pendingCount() const { return pending_.size(); }

private:
    void activate(reason::Goal g);
    void finish(BtStatus status);

    reason::GoalQueue& queue_;
    ExecutorFactory factory_;
    FactSink outcomes_;
    int hysteresis_;
    std::vector<reason::Goal> pending_;
    std::optional<reason::Goal> active_;
    std::unique_ptr<IBehaviorExecutor> exec_;
    std::string activeId_;
    uint64_t nextTask_ = 1;
};

}  // namespace krbot::exec
