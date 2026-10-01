#pragma once
// L2 sensing loop (doc v3 §7): polls sources, maps observations, pushes facts into L3.

#include "knowledge/FactStore.hpp"
#include "percep/Observation.hpp"

#include <atomic>
#include <chrono>
#include <memory>
#include <thread>
#include <vector>

namespace krbot::percep {

class PerceptionLoop {
public:
    PerceptionLoop(knowledge::FactStore& facts, std::chrono::milliseconds period);
    ~PerceptionLoop();

    void addSource(std::unique_ptr<ISensorSource> s);  // before start()
    void start();
    void stop();

private:
    void run();

    knowledge::FactStore& facts_;
    std::chrono::milliseconds period_;
    std::vector<std::unique_ptr<ISensorSource>> sources_;
    std::atomic<bool> running_{false};
    std::thread thread_;
};

}  // namespace krbot::percep
