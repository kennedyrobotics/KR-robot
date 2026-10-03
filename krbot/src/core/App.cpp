#include "core/App.hpp"

#include "common/Json.hpp"
#include "common/Log.hpp"
#include "core/MonitorServer.hpp"
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
    startTime_ = std::chrono::steady_clock::now();

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

    // L5 - watchdog first so the manual loop is supervised from its first tick
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
    startMonitor();

    KLOG_INFO(kTag, "krbot started - motors: {}", drivers_->motorDescription());
}

void App::arbiterLoop() {
    const auto period = std::chrono::milliseconds(cfg_.getInt("arbiter.period_ms", 50));
    auto next = std::chrono::steady_clock::now();
    while (arbiterRunning_) {
        arbiter_->tick();
        {
            const auto g = arbiter_->activeGoal();
            std::lock_guard lock(goalMutex_);
            goalSummary_ = g ? GoalSummary{g->type, g->target, arbiter_->activeTaskId(), g->priority, arbiter_->pendingCount()}
                             : GoalSummary{"", "", "", 0, arbiter_->pendingCount()};
        }
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
    KLOG_INFO(kTag, "stopped - motors zeroed");
    if (monitor_) {
        common::setLogSink({});  // before the server goes away
        monitor_->stop();
        monitor_.reset();
    }
}

void App::startMonitor() {
    if (!cfg_.getBool("monitor.enabled", true)) return;
    MonitorServer::Options o;
    o.bind = cfg_.getString("monitor.bind", o.bind);
    o.port = static_cast<uint16_t>(cfg_.getInt("monitor.port", o.port));
    o.rateHz = static_cast<int>(cfg_.getInt("monitor.rate_hz", o.rateHz));
    monitor_ = std::make_unique<MonitorServer>(
        o, [this] { return snapshotJson(); },
        [this](const std::string& cmd, const std::string& peer) -> std::string {
            if (cmd == "estop") {
                KLOG_WARN(kTag, "E-STOP requested by monitor client {}", peer);
                requestEstop("monitor e-stop");
                return R"({"type":"ack","cmd":"estop"})";
            }
            if (cmd == "ping") return R"({"type":"pong"})";
            return {};
        });
    try {
        monitor_->start();
    } catch (const std::exception& e) {
        KLOG_ERROR(kTag, "monitor server disabled: {}", e.what());  // robot keeps running without it
        monitor_.reset();
        return;
    }
    auto* srv = monitor_.get();
    common::setLogSink([srv](common::LogLevel lvl, const std::string& line) { srv->publishLog(common::toString(lvl), line); });
    KLOG_INFO(kTag, "monitor server on {}:{}", o.bind, srv->port());
}

std::string App::snapshotJson() const {
    namespace json = common::json;
    if (!manual_) return R"("mode":"STARTING")";
    const auto s = manual_->status();
    GoalSummary g;
    {
        std::lock_guard lock(goalMutex_);
        g = goalSummary_;
    }
    const auto& in = s.inputs;
    const double uptime = std::chrono::duration<double>(std::chrono::steady_clock::now() - startTime_).count();
    const bool dry = cfg_.getString("motor.driver", "yahboom") == "sim";
    return std::format(
        R"("version":"0.1.0","uptime_s":{:.1f},"dry_run":{},"mode":{},"mode_reason":{},)"
        R"("out":{{"left":{},"right":{}}},"max_output":{},)"
        R"("inputs":{{"lx":{:.3f},"ly":{:.3f},"rx":{:.3f},"ry":{:.3f},"deadman":{},"arm":{},"estop":{},"link":{}}},)"
        R"("gamepad":{{"connected":{},"name":{},"disconnects":{}}},)"
        R"("motors":{{"healthy":{},"desc":{}}},"loop_hz":{:.1f},)"
        R"("watchdog":{{"trips":{},"timeout_ms":{}}},"facts":{},)"
        R"("reasoner":{{"ticks":{},"saturated":{},"deltas":{}}},)"
        R"("goal":{{"type":{},"target":{},"task":{},"priority":{},"pending":{}}},"monitor_clients":{},"pad":{})",
        uptime, json::boolean(dry), json::quote(exec::toString(s.mode)), json::quote(s.reason), s.out.left,
        s.out.right, cfg_.getInt("teleop.max_output", 1800), in.lx, in.ly, in.rx, in.ry, json::boolean(in.deadman),
        json::boolean(in.arm), json::boolean(in.estop), json::boolean(in.linkOk), json::boolean(s.padConnected),
        json::quote(s.padName), s.padDisconnects, json::boolean(s.motorsHealthy),
        json::quote(drivers_ ? drivers_->motorDescription() : ""), s.loopHz, watchdog_ ? watchdog_->trips() : 0,
        cfg_.getInt("watchdog.timeout_ms", 200), facts_ ? facts_->size() : 0, reason_ ? reason_->ticks() : 0,
        reason_ ? reason_->saturatedTicks() : 0, reason_ ? reason_->deltasSynced() : 0, json::quote(g.type),
        json::quote(g.target), json::quote(g.task), g.priority, g.pending, monitor_ ? monitor_->clientCount() : 0,
        padJson(s));
}

// Full gamepad state for the monitor's Controller tab. Keys are linux evdev codes (decimal strings, after any
// button remap); "buttons" values are [pressed, presses-since-connect] so a tap between snapshots still shows.
std::string App::padJson(const exec::ManualDriveStatus& s) {
    std::string keys, buttons, axes;
    for (const int k : s.padKeys) keys += std::format("{}{}", keys.empty() ? "" : ",", k);
    for (const int k : s.padKeys) {
        const auto it = s.pad.presses.find(k);
        buttons += std::format(R"({}"{}":[{},{}])", buttons.empty() ? "" : ",", k, s.pad.button(k) ? 1 : 0,
                               it == s.pad.presses.end() ? 0u : it->second);
    }
    for (const auto& [code, v] : s.pad.axes) axes += std::format(R"({}"{}":{:.3f})", axes.empty() ? "" : ",", code, v);
    return std::format(R"({{"id":{},"remap":{},"keys":[{}],"buttons":{{{}}},"axes":{{{}}}}})",
                       common::json::quote(s.padId), common::json::boolean(s.padRemapped), keys, buttons, axes);
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
