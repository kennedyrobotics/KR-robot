#include "exec/StubExecutor.hpp"

namespace krbot::exec {

BtStatus StubExecutor::tick() {
    if (goal_.type == "Idle" || goal_.type == "HoldPosition") return BtStatus::Running;
    return BtStatus::Failure;
}

ExecutorFactory stubExecutorFactory() {
    return [](const reason::Goal& g) { return std::make_unique<StubExecutor>(g); };
}

}  // namespace krbot::exec
