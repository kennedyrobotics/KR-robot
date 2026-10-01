#pragma once
// Executor seam for one active Goal. Phase 1: a BehaviorTree.CPP implementation loads the goal
// type's subtree (doc v3 §6: GoalTypeToTreeFile["HoldPosition"] -> "trees/hold_position.xml"),
// binds target into the blackboard, and maps tickOnce()/haltTree() onto tick()/halt().

#include "reason/Goal.hpp"

#include <functional>
#include <memory>

namespace krbot::exec {

enum class BtStatus { Running, Success, Failure };

class IBehaviorExecutor {
public:
    virtual BtStatus tick() = 0;
    virtual void halt() = 0;
    virtual ~IBehaviorExecutor() = default;
};

using ExecutorFactory = std::function<std::unique_ptr<IBehaviorExecutor>(const reason::Goal&)>;

}  // namespace krbot::exec
