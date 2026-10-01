#include "exec/GoalArbiter.hpp"

#include "common/Log.hpp"

#include <algorithm>

namespace krbot::exec {

static constexpr auto kTag = "L5.arbiter";

GoalArbiter::GoalArbiter(reason::GoalQueue& queue, ExecutorFactory factory, FactSink outcomes, int hysteresis)
    : queue_(queue), factory_(std::move(factory)), outcomes_(std::move(outcomes)), hysteresis_(hysteresis) {}

std::optional<reason::Goal> GoalArbiter::activeGoal() const { return active_; }

void GoalArbiter::activate(reason::Goal g) {
    activeId_ = "task" + std::to_string(nextTask_++);
    KLOG_INFO(kTag, "activate {} {}({}) prio={}", activeId_, g.type, g.target, g.priority);
    exec_ = factory_(g);
    active_ = std::move(g);
    if (outcomes_) {
        knowledge::Fact f;
        f.subject = activeId_;
        f.predicate = "goal";
        f.object = active_->type + ":" + active_->target;
        f.source = knowledge::FactSource::Execution;
        outcomes_(std::move(f));
    }
}

void GoalArbiter::finish(BtStatus status) {
    if (outcomes_) {
        knowledge::Fact f;  // doc v3 §6 TaskOutcome
        f.subject = activeId_;
        f.predicate = "outcome";
        f.object = std::string(status == BtStatus::Success ? "Success" : "Blocked");
        f.source = knowledge::FactSource::Execution;
        outcomes_(std::move(f));
    }
    KLOG_INFO(kTag, "{} {} -> {}", activeId_, active_->type, status == BtStatus::Success ? "Success" : "Failure");
    exec_.reset();
    active_.reset();
    activeId_.clear();
}

void GoalArbiter::tick() {
    for (auto& g : queue_.drain()) pending_.push_back(std::move(g));
    // highest priority first; stable so equal priorities stay FIFO
    std::stable_sort(pending_.begin(), pending_.end(),
                     [](const reason::Goal& a, const reason::Goal& b) { return a.priority > b.priority; });

    if (!pending_.empty()) {
        auto& best = pending_.front();
        if (!active_ || best.priority > active_->priority + hysteresis_) {
            if (active_) {
                KLOG_INFO(kTag, "pre-empt {} (prio {}) by {} (prio {})", active_->type, active_->priority, best.type,
                          best.priority);
                exec_->halt();
                finish(BtStatus::Failure);
            }
            auto g = std::move(pending_.front());
            pending_.erase(pending_.begin());
            activate(std::move(g));
        }
    }

    if (exec_) {
        const auto st = exec_->tick();
        if (st != BtStatus::Running) finish(st);
    }
}

}  // namespace krbot::exec
