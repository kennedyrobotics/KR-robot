#include "percep/PerceptionLoop.hpp"

#include "common/Log.hpp"
#include "percep/ObservationMapper.hpp"

namespace krbot::percep {

PerceptionLoop::PerceptionLoop(knowledge::FactStore& facts, std::chrono::milliseconds period)
    : facts_(facts), period_(period) {}

PerceptionLoop::~PerceptionLoop() { stop(); }

void PerceptionLoop::addSource(std::unique_ptr<ISensorSource> s) { sources_.push_back(std::move(s)); }

void PerceptionLoop::start() {
    if (running_.exchange(true)) return;
    thread_ = std::thread([this] { run(); });
}

void PerceptionLoop::stop() {
    if (!running_.exchange(false)) return;
    if (thread_.joinable()) thread_.join();
}

void PerceptionLoop::run() {
    auto next = std::chrono::steady_clock::now();
    while (running_) {
        for (auto& src : sources_) {
            for (const auto& obs : src->poll()) {
                auto m = mapObservation(obs);
                if (m.functional)
                    facts_.upsert(std::move(m.fact));
                else
                    facts_.add(std::move(m.fact));
            }
        }
        next += period_;
        std::this_thread::sleep_until(next);
    }
}

}  // namespace krbot::percep
