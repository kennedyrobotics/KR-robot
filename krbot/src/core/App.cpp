#include "core/App.hpp"

#include "common/Log.hpp"
#include "driver/DriverRegistry.hpp"
#include "exec/GoalArbiter.hpp"
#include "exec/ManualDrive.hpp"
#include "exec/StubExecutor.hpp"
#include "exec/Watchdog.hpp"
#include "knowledge/FactStore.hpp"
#include "percep/MotorHealthSource.hpp"
#include "percep/PerceptionLoop.hpp"
#include "reason/NullReasoner.hpp"
#include "reason/ReasoningLoop.hpp"

namespace krbot::core {

using namespace std::chrono_literals;
static constexpr auto kTag = "core";

struct App::Queues {
    reason::GoalQueue goals;
};

App::App(common::Config cfg) : cfg_(std::move(cfg)), queues_(std::make_unique<Queues>()) {}

App::~App() { stop(); }

void App::start() {
    if (started_) return;
    started_ = true;

    // L1
    drivers_ = driver::DriverRegistry::fromConfig(cfg_);
    auto& motors = drivers_->motors();

    // L3
    facts_ = std::make_unique<knowledge::FactStore>();
    facts_->start();
    {
        knowledge::Fact seed;  // configured-baseline tier (doc v3 §4)
        seed.subject = "robot";
        seed.predicate = "platform";
        seed.object = std::string("trackedSkidSteer");
        seed.source = knowledge::FactSource::ConfigSeed;
        facts_->upsert(seed);
    }

    // L2
    percep_ = std::make_unique<percep::PerceptionLoop>(*facts_, std::chrono::milliseconds(cfg_.getInt("percep.period_ms", 100)));
    percep_->addSource(std::make_unique<percep::MotorHealthSource>(motors));
    percep_->start();

    // L4
    reason::ReasonerOutputs outs{
        [this](reason::Goal g) { queues_->goals.push(std::move(g)); },
        [this](knowledge::Fact f) {
            f.source = knowledge::FactSource::Inferred;
            facts_->add(std::move(f));
        }};
    reason_ = std::make_unique<reason::ReasoningLoop>(facts_->deltas(), std::make_unique<reason::NullReasoner>(outs),
                                                      std::chrono::milliseconds(cfg_.getInt("reason.period_ms", 50)),
                                                      static_cast<int>(cfg_.getInt("reason.run_limit", 300)));
    reason_->start();

    // L5 — watchdog first so the manual loop is supervised from its first tick
    watchdog_ = std::make_unique<exec::Watchdog>(
        motors, std::chrono::milliseconds(cfg_.getInt("watchdog.timeout_ms", 200)),
        [this](const char* why) {
            if (manual_) manual_->requestEstop(std::string("watchdog: ") + why);
        });
    exec::ManualDrive::Options mo;
    auto& t = mo.teleop;
    t.maxOutput = static_cast<int>(cfg_.getInt("teleop.max_output", t.maxOutput));
    t.stickDeadband = static_cast<float>(cfg_.getDouble("teleop.stick_deadband", t.stickDeadband));
    t.expo = static_cast<float>(cfg_.getDouble("teleop.expo", t.expo));
    t.turnScale = static_cast<float>(cfg_.getDouble("teleop.turn_scale", t.turnScale));
    t.slewPerS = static_cast<float>(cfg_.getDouble("teleop.slew_per_s", t.slewPerS));
    t.tank = cfg_.getBool("teleop.tank", t.tank);
    t.invertLeft = cfg_.getBool("teleop.invert_left", t.invertLeft);
    t.invertRight = cfg_.getBool("teleop.invert_right", t.invertRight);
    t.leftChannel = static_cast<int>(cfg_.getInt("teleop.left_channel", t.leftChannel));
    t.rightChannel = static_cast<int>(cfg_.getInt("teleop.right_channel", t.rightChannel));
    mo.gamepadPath = cfg_.getString("input.device", "");
    mo.rateHz = static_cast<int>(cfg_.getInt("teleop.rate_hz", 50));
    manual_ = std::make_unique<exec::ManualDrive>(motors, facts_.get(), watchdog_.get(), mo);

    arbiter_ = std::make_unique<exec::GoalArbiter>(
        queues_->goals, exec::stubExecutorFactory(), [this](knowledge::Fact f) { facts_->upsert(std::move(f)); },
        static_cast<int>(cfg_.getInt("arbiter.hysteresis", 10)));
    queues_->goals.push(reason::Goal{"Idle", "", 0, knowledge::FactSource::ConfigSeed});

    watchdog_->start();
    manual_->start();
    arbiterRunning_ = true;
    arbiterThread_ = std::thread([this] { arbiterLoop(); });

    KLOG_INFO(kTag, "krbot started — motors: {}", drivers_->motorDescription());
}

void App::arbiterLoop() {
    const auto period = std::chrono::milliseconds(cfg_.getInt("arbiter.period_ms", 50));
    auto next = std::chrono::steady_clock::now();
    while (arbiterRunning_) {
        arbiter_->tick();
        next += period;
        std::this_thread::sleep_until(next);
    }
}

void App::stop() {
    if (!started_) return;
    started_ = false;
    KLOG_INFO(kTag, "stopping");
    if (manual_) manual_->stop();  // zeros motors first
    if (watchdog_) watchdog_->stop();
    arbiterRunning_ = false;
    if (arbiterThread_.joinable()) arbiterThread_.join();
    queues_->goals.close();
    if (reason_) reason_->stop();
    if (percep_) percep_->stop();
    if (facts_) facts_->stop();
    if (drivers_) {
        try {
            drivers_->motors().stopAll();
        } catch (...) {
        }
    }
    KLOG_INFO(kTag, "stopped — motors zeroed");
}

void App::requestEstop(const char* reason) {
    if (manual_) manual_->requestEstop(reason);
}

void App::logStatus() const {
    if (!manual_) return;
    const auto s = manual_->status();
    const auto goal = arbiter_ ? arbiter_->activeGoal() : std::nullopt;
    KLOG_INFO(kTag, "{:<8} L={:+5d} R={:+5d} | pad {} | motors {} | loop {:.0f} Hz | facts {} | goal {} | {}",
              exec::toString(s.mode), s.out.left, s.out.right, s.padConnected ? s.padName : "none",
              s.motorsHealthy ? "ok" : "FAULT", s.loopHz, facts_ ? facts_->size() : 0,
              goal ? goal->type : "-", s.reason);
}

}  // namespace krbot::core
