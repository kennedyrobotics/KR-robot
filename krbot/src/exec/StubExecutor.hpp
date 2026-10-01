#pragma once
// Stand-in executors until BehaviorTree.CPP (Phase 1). None of them commands motion yet — the
// only motion path in this increment is ManualDrive (direct L5->L1).
//   Idle, HoldPosition -> Running until pre-empted (holding = zero output, which is the default)
//   anything else      -> Failure on first tick (no tree for that goal type yet)

#include "exec/IBehaviorExecutor.hpp"

namespace krbot::exec {

class StubExecutor final : public IBehaviorExecutor {
public:
    explicit StubExecutor(reason::Goal g) : goal_(std::move(g)) {}
    BtStatus tick() override;
    void halt() override { halted_ = true; }
    bool halted() const { return halted_; }

private:
    reason::Goal goal_;
    bool halted_ = false;
};

ExecutorFactory stubExecutorFactory();

}  // namespace krbot::exec
