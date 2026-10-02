#pragma once
// L5 heartbeat/watchdog supervisor - doc v3 §6/§7: "a separate, minimal, independently-tested
// watchdog thread" and "the only hard safety backstop". It owns no logic beyond: if the control
// loop stops kicking for `timeout`, call IMotorController::stopAll() directly (independent of the
// arbiter and the control loop) and report the trip. Re-arming after a trip stays a teleop action.

#include "driver/IMotorController.hpp"

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <functional>
#include <mutex>
#include <thread>

namespace krbot::exec {

class Watchdog {
public:
    using Clock = std::chrono::steady_clock;
    using TripHandler = std::function<void(const char* reason)>;

    Watchdog(driver::IMotorController& motors, std::chrono::milliseconds timeout, TripHandler onTrip = {});
    ~Watchdog();

    void start();
    void stop();
    void kick();  // called every control-loop tick

    // Pure check, exposed for tests: returns true if it tripped on this call.
    bool check(Clock::time_point now);

    std::size_t trips() const { return trips_; }
    bool tripped() const { return tripped_; }

private:
    void run();

    driver::IMotorController& motors_;
    std::chrono::milliseconds timeout_;
    TripHandler onTrip_;
    std::atomic<Clock::rep> lastKick_;
    std::atomic<bool> tripped_{false};
    std::atomic<std::size_t> trips_{0};
    std::atomic<bool> running_{false};
    std::mutex m_;
    std::condition_variable cv_;
    std::thread thread_;
};

}  // namespace krbot::exec
