#pragma once
// ManualDrive — the direct L5->L1 path (doc v3 §6): gamepad -> TeleopController -> IMotorController,
// bypassing L3/L4 and the GoalArbiter, always highest priority. Runs its own 50 Hz thread, kicks
// the Watchdog every tick, and reports mode changes / link events to L3 as facts.
//
// Thread-safety: requestEstop() and status() may be called from any thread.

#include "driver/IMotorController.hpp"
#include "exec/InputDriver.hpp"
#include "exec/TeleopSafety.hpp"
#include "knowledge/FactStore.hpp"

#include <atomic>
#include <chrono>
#include <mutex>
#include <string>
#include <thread>

namespace krbot::exec {

class Watchdog;

struct ManualDriveStatus {
    TeleopMode mode = TeleopMode::Disarmed;
    std::string reason;
    TeleopOutput out;
    bool padConnected = false;
    std::string padName;
    bool motorsHealthy = false;
    double loopHz = 0;
    std::size_t padDisconnects = 0;
};

class ManualDrive {
public:
    struct Options {
        TeleopConfig teleop;
        std::string gamepadPath;  // empty = auto-detect
        int rateHz = 50;
    };

    ManualDrive(driver::IMotorController& motors, knowledge::FactStore* facts, Watchdog* watchdog, Options opts);
    ~ManualDrive();

    void start();
    void stop();  // zeros the motors

    void requestEstop(std::string reason);  // thread-safe; applied on the next tick
    ManualDriveStatus status() const;

    // One control step with given inputs — the loop calls this; public for tests.
    TeleopOutput step(const TeleopInputs& in, float dt);

private:
    void run();
    TeleopInputs readInputs(std::chrono::steady_clock::time_point now);
    void publishFact(const std::string& subject, const std::string& predicate, const std::string& object);

    driver::IMotorController& motors_;
    knowledge::FactStore* facts_;
    Watchdog* watchdog_;
    Options opts_;
    TeleopController ctl_;
    InputDriver input_;
    std::chrono::steady_clock::time_point nextPadTry_{};

    std::atomic<bool> running_{false};
    std::thread thread_;
    std::mutex estopMutex_;
    std::string pendingEstop_;

    mutable std::mutex statusMutex_;
    ManualDriveStatus status_;
    bool motorFaultLatched_ = false;
};

}  // namespace krbot::exec
