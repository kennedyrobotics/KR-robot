#pragma once
// core - wiring, config, lifecycle (doc v3 §9). Owns every layer and the queues between them.
//
// Start order (bottom-up):  L1 drivers -> L3 FactStore -> L2 perception -> L4 reasoning ->
//                           L5 watchdog + manual drive + arbiter loop
// Stop order (safety first): L5 manual drive (zeros motors) -> watchdog -> arbiter -> L4 -> L2 -> L3
//                           -> final stopAll()

#include "common/Config.hpp"

#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

namespace krbot::driver { class DriverRegistry; }
namespace krbot::knowledge { class FactStore; }
namespace krbot::percep { class PerceptionLoop; }
namespace krbot::reason { class ReasoningLoop; }
namespace krbot::exec { class ManualDrive; class Watchdog; class GoalArbiter; struct ManualDriveStatus; }

namespace krbot::core {

class MonitorServer;

class App {
public:
    explicit App(common::Config cfg);
    ~App();

    void start();
    void stop();
    void requestEstop(const char* reason);
    void logStatus() const;
    std::string snapshotJson() const;  // status members for the monitor protocol (thread-safe)
    static std::string padJson(const exec::ManualDriveStatus& s);  // the "pad" member of snapshotJson
    std::string batteryJson(const exec::ManualDriveStatus& s) const;  // the "battery" member
    std::string boardJson(const exec::ManualDriveStatus& s) const;    // the "board" member

private:
    void arbiterLoop();
    void startMonitor();

    struct GoalSummary {
        std::string type, target, task;
        int priority = 0;
        std::size_t pending = 0;
    };
    mutable std::mutex goalMutex_;
    GoalSummary goalSummary_;  // written by the arbiter thread after each tick
    std::chrono::steady_clock::time_point startTime_;
    std::unique_ptr<MonitorServer> monitor_;

    common::Config cfg_;
    std::unique_ptr<driver::DriverRegistry> drivers_;
    std::unique_ptr<knowledge::FactStore> facts_;
    std::unique_ptr<percep::PerceptionLoop> percep_;
    std::unique_ptr<reason::ReasoningLoop> reason_;
    std::unique_ptr<exec::Watchdog> watchdog_;
    std::unique_ptr<exec::ManualDrive> manual_;
    std::unique_ptr<exec::GoalArbiter> arbiter_;
    struct Queues;
    std::unique_ptr<Queues> queues_;
    std::atomic<bool> arbiterRunning_{false};
    std::thread arbiterThread_;
    bool started_ = false;
};

}  // namespace krbot::core
