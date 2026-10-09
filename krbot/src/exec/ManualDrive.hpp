#pragma once
// ManualDrive - the direct L5->L1 path (doc v3 §6): gamepad -> TeleopController -> IMotorController,
// bypassing L3/L4 and the GoalArbiter, always highest priority. Runs its own 50 Hz thread, kicks
// the Watchdog every tick, and reports mode changes / link events to L3 as facts.
//
// Thread-safety: requestEstop() and status() may be called from any thread.
//
// Gamepad discovery runs on its own thread: scanning /dev/input opens every input device, and
// opening an autosuspended USB HID device can block for tens of ms. Doing that on the control
// thread cost ~3 ticks/s at 50 Hz, so the discovery thread opens the pad and hands the open
// InputDriver over; the control loop only ever polls an already-open device (non-blocking).

#include "driver/IMotorController.hpp"
#include "exec/Battery.hpp"
#include "exec/InputDriver.hpp"
#include "exec/TeleopSafety.hpp"
#include "knowledge/FactStore.hpp"

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <memory>
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
    TeleopInputs inputs;  // last inputs fed to the controller (for monitoring)
    // Full pad state for the monitor's Controller tab (maintenance check of every button/axis).
    GamepadState pad;
    std::set<int> padKeys;
    std::string padId;
    bool padRemapped = false;
    // Motor board self-report (battery, liveness) and the battery state derived from it.
    driver::BoardStatus board;
    BatteryState battery = BatteryState::Unknown;
    bool batteryArmBlocked = false;
};

class ManualDrive {
public:
    struct Options {
        TeleopConfig teleop;
        std::string gamepadPath;  // empty = auto-detect
        int rateHz = 50;
        BatteryConfig battery;
        double boardSilentS = 3.0;  // warn when the board has not replied for this long
    };

    ManualDrive(driver::IMotorController& motors, knowledge::FactStore* facts, Watchdog* watchdog, Options opts);
    ~ManualDrive();

    void start();
    void stop();  // zeros the motors

    void requestEstop(std::string reason);  // thread-safe; applied on the next tick
    ManualDriveStatus status() const;
    const Options& options() const { return opts_; }

    // One control step with given inputs - the loop calls this; public for tests.
    TeleopOutput step(const TeleopInputs& in, float dt);

private:
    void run();
    void discoveryLoop();
    TeleopInputs readInputs();
    void publishFact(const std::string& subject, const std::string& predicate, const std::string& object);

    driver::IMotorController& motors_;
    knowledge::FactStore* facts_;
    Watchdog* watchdog_;
    Options opts_;
    TeleopController ctl_;
    std::unique_ptr<InputDriver> input_;  // control thread only

    // discovery thread -> control thread hand-off
    std::thread discovery_;
    std::mutex padMutex_;
    std::condition_variable padCv_;
    std::unique_ptr<InputDriver> pendingPad_;
    bool needPad_ = true;  // guarded by padMutex_

    std::atomic<bool> running_{false};
    std::thread thread_;
    std::mutex estopMutex_;
    std::string pendingEstop_;

    mutable std::mutex statusMutex_;
    ManualDriveStatus status_;
    bool motorFaultLatched_ = false;
    BatteryMonitor battery_;    // control thread only
    bool boardSilent_ = false;  // control thread only
    void updateBoard(TeleopInputs& in);
};

}  // namespace krbot::exec
