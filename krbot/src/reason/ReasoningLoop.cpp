#include "reason/ReasoningLoop.hpp"

#include "common/Log.hpp"

namespace krbot::reason {

static constexpr auto kTag = "L4.reason";

ReasoningLoop::ReasoningLoop(common::ThreadSafeQueue<knowledge::FactDelta>& deltas,
                             std::unique_ptr<IReasoner> reasoner, std::chrono::milliseconds period, int runLimit)
    : deltas_(deltas), reasoner_(std::move(reasoner)), period_(period), runLimit_(runLimit) {}

ReasoningLoop::~ReasoningLoop() { stop(); }

void ReasoningLoop::start() {
    if (running_.exchange(true)) return;
    thread_ = std::thread([this] { run(); });
}

void ReasoningLoop::stop() {
    if (!running_.exchange(false)) return;
    if (thread_.joinable()) thread_.join();
}

void ReasoningLoop::run() {
    auto next = std::chrono::steady_clock::now();
    while (running_) {
        for (const auto& d : deltas_.drain()) reasoner_->syncDelta(d);
        const auto fired = reasoner_->tick(runLimit_);
        if (fired >= runLimit_) {
            ++saturated_;
            KLOG_WARN(kTag, "rule run limit {} hit — possible runaway rule pair", runLimit_);
        }
        ++ticks_;
        next += period_;
        std::this_thread::sleep_until(next);
    }
}

}  // namespace krbot::reason
