#pragma once
// L4 thread (doc v3 §7): per tick, strictly phased —
//   drain FactDelta queue -> syncDelta each -> bounded tick(runLimit) -> (goals already queued by
//   the reasoner's outputs) . No re-entry of syncDelta during tick.

#include "common/ThreadSafeQueue.hpp"
#include "knowledge/Fact.hpp"
#include "reason/IReasoner.hpp"

#include <atomic>
#include <chrono>
#include <memory>
#include <thread>

namespace krbot::reason {

class ReasoningLoop {
public:
    ReasoningLoop(common::ThreadSafeQueue<knowledge::FactDelta>& deltas, std::unique_ptr<IReasoner> reasoner,
                  std::chrono::milliseconds period, int runLimit);
    ~ReasoningLoop();
    void start();
    void stop();

    IReasoner& reasoner() { return *reasoner_; }
    long long ticks() const { return ticks_; }
    long long saturatedTicks() const { return saturated_; }

private:
    void run();

    common::ThreadSafeQueue<knowledge::FactDelta>& deltas_;
    std::unique_ptr<IReasoner> reasoner_;
    std::chrono::milliseconds period_;
    int runLimit_;
    std::atomic<bool> running_{false};
    std::atomic<long long> ticks_{0};
    std::atomic<long long> saturated_{0};
    std::thread thread_;
};

}  // namespace krbot::reason
