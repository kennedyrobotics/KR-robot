#include "exec/Watchdog.hpp"

#include "common/Log.hpp"

namespace krbot::exec {

static constexpr auto kTag = "L5.watchdog";

Watchdog::Watchdog(driver::IMotorController& motors, std::chrono::milliseconds timeout, TripHandler onTrip)
    : motors_(motors), timeout_(timeout), onTrip_(std::move(onTrip)),
      lastKick_(Clock::now().time_since_epoch().count()) {}

Watchdog::~Watchdog() { stop(); }

void Watchdog::start() {
    if (running_.exchange(true)) return;
    kick();
    thread_ = std::thread([this] { run(); });
}

void Watchdog::stop() {
    if (!running_.exchange(false)) return;
    cv_.notify_all();
    if (thread_.joinable()) thread_.join();
}

void Watchdog::kick() {
    lastKick_ = Clock::now().time_since_epoch().count();
    tripped_ = false;
}

bool Watchdog::check(Clock::time_point now) {
    const Clock::time_point last{Clock::duration{lastKick_.load()}};
    if (now - last < timeout_ || tripped_) return false;
    tripped_ = true;
    ++trips_;
    try {
        motors_.stopAll();  // direct to L1 — the backstop does not depend on any other thread
    } catch (const std::exception& e) {
        KLOG_ERROR(kTag, "stopAll failed during trip: {}", e.what());
    }
    KLOG_ERROR(kTag, "control loop stalled > {} ms — motors stopped", timeout_.count());
    if (onTrip_) onTrip_("control loop stalled");
    return true;
}

void Watchdog::run() {
    std::unique_lock lock(m_);
    const auto period = timeout_ / 4;
    while (running_) {
        cv_.wait_for(lock, period, [&] { return !running_; });
        if (!running_) break;
        check(Clock::now());
    }
}

}  // namespace krbot::exec
