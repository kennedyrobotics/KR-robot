#include "exec/ManualDrive.hpp"

#include "common/Log.hpp"
#include "exec/Watchdog.hpp"

#include <algorithm>
#include <linux/input-event-codes.h>

namespace krbot::exec {

using namespace std::chrono_literals;
using Clock = std::chrono::steady_clock;
static constexpr auto kTag = "L5.manual";

ManualDrive::ManualDrive(driver::IMotorController& motors, knowledge::FactStore* facts, Watchdog* watchdog,
                         Options opts)
    : motors_(motors), facts_(facts), watchdog_(watchdog), opts_(std::move(opts)), ctl_(opts_.teleop) {
    status_.reason = ctl_.reason();
    status_.motorsHealthy = motors_.isHealthy();
}

ManualDrive::~ManualDrive() { stop(); }

void ManualDrive::start() {
    if (running_.exchange(true)) return;
    discovery_ = std::thread([this] { discoveryLoop(); });
    thread_ = std::thread([this] { run(); });
}

void ManualDrive::stop() {
    if (!running_.exchange(false)) return;
    padCv_.notify_all();
    if (thread_.joinable()) thread_.join();
    if (discovery_.joinable()) discovery_.join();
    try {
        motors_.stopAll();
    } catch (const std::exception& e) {
        KLOG_ERROR(kTag, "stopAll on shutdown failed: {}", e.what());
    }
    input_.reset();
    std::lock_guard lock(padMutex_);
    pendingPad_.reset();
}

void ManualDrive::discoveryLoop() {
    std::unique_lock lock(padMutex_);
    while (running_) {
        if (needPad_ && !pendingPad_) {
            lock.unlock();  // the slow part (scan + open) happens without the lock
            auto pad = std::make_unique<InputDriver>();
            const bool ok = pad->open(opts_.gamepadPath);
            lock.lock();
            if (!ok) {
                // no pad yet: back off 1 s (the predicate below would be true at once -> spin)
                padCv_.wait_for(lock, 1s, [&] { return !running_.load(); });
                continue;
            }
            pendingPad_ = std::move(pad);
            needPad_ = false;
        }
        // have a pad: sleep until the control loop reports it lost (or shutdown)
        padCv_.wait_for(lock, 1s, [&] { return !running_ || (needPad_ && !pendingPad_); });
    }
}

void ManualDrive::requestEstop(std::string reason) {
    std::lock_guard lock(estopMutex_);
    pendingEstop_ = std::move(reason);
}

ManualDriveStatus ManualDrive::status() const {
    std::lock_guard lock(statusMutex_);
    return status_;
}

void ManualDrive::publishFact(const std::string& subject, const std::string& predicate, const std::string& object) {
    if (!facts_) return;
    knowledge::Fact f;
    f.subject = subject;
    f.predicate = predicate;
    f.object = object;
    f.source = knowledge::FactSource::Operator;
    facts_->upsert(std::move(f));
}

TeleopOutput ManualDrive::step(const TeleopInputs& in, float dt) {
    {
        std::lock_guard lock(estopMutex_);
        if (!pendingEstop_.empty()) {
            ctl_.forceEstop(pendingEstop_);
            KLOG_WARN(kTag, "E-STOP: {}", pendingEstop_);
            pendingEstop_.clear();
        }
    }
    const bool healthy = motors_.isHealthy();
    if (!healthy && !motorFaultLatched_) {
        KLOG_ERROR(kTag, "motor controller unhealthy - E-STOP latched");
        publishFact("motorBoard", "fault", "linkLost");
    }
    motorFaultLatched_ = !healthy;

    const auto prevMode = ctl_.mode();
    auto out = ctl_.update(in, dt);
    if (!healthy && ctl_.mode() != TeleopMode::Estop) {
        // held every tick, not just on the transition: START must not arm with no motor link
        ctl_.forceEstop("motor link lost");
        out = {};
    }
    if (ctl_.mode() != prevMode) {
        KLOG_INFO(kTag, "{} -> {} ({})", toString(prevMode), toString(ctl_.mode()), ctl_.reason());
        publishFact("robot", "teleopMode", toString(ctl_.mode()));
    }

    if (healthy) {
        try {
            motors_.setAllChannels(ctl_.channels(out));
        } catch (const std::exception& e) {
            KLOG_ERROR(kTag, "motor write failed: {}", e.what());
            ctl_.forceEstop("motor write failed");
        }
    }

    std::lock_guard lock(statusMutex_);
    status_.mode = ctl_.mode();
    status_.reason = ctl_.reason();
    status_.out = out;
    status_.motorsHealthy = healthy;
    status_.inputs = in;
    return out;
}

TeleopInputs ManualDrive::readInputs() {
    if (!input_) {
        std::unique_lock lock(padMutex_, std::try_to_lock);  // never wait on the discovery thread
        if (lock.owns_lock() && pendingPad_) {
            input_ = std::move(pendingPad_);
            lock.unlock();
            publishFact("gamepad", "linkHealthy", "true");
            std::lock_guard sl(statusMutex_);
            status_.padConnected = true;
            status_.padName = input_->name();
        }
    }
    TeleopInputs in;
    in.linkOk = false;
    if (!input_) return in;

    if (input_->poll(0) == InputDriver::PollResult::LinkLost) {
        KLOG_WARN(kTag, "gamepad link lost - SAFE STOP");
        input_.reset();
        {
            std::lock_guard lock(padMutex_);
            needPad_ = true;
        }
        padCv_.notify_all();
        publishFact("gamepad", "linkHealthy", "false");
        std::lock_guard lock(statusMutex_);
        status_.padConnected = false;
        ++status_.padDisconnects;
        return in;
    }
    const auto& s = input_->state();
    in.linkOk = true;
    in.lx = s.axis(ABS_X);
    in.ly = s.axis(ABS_Y);
    in.rx = s.axis(ABS_RX);
    in.ry = s.axis(ABS_RY);
    in.deadman = s.button(BTN_TL);
    in.arm = s.button(BTN_START);
    in.estop = s.button(BTN_EAST) || s.button(BTN_MODE);
    return in;
}

void ManualDrive::run() {
    const auto period = std::chrono::microseconds(1'000'000 / std::max(1, opts_.rateHz));
    auto prev = Clock::now();
    auto next = prev + period;
    auto rateT0 = prev;
    int ticks = 0;
    publishFact("robot", "teleopMode", toString(ctl_.mode()));
    while (running_) {
        const auto now = Clock::now();
        const float dt = std::chrono::duration<float>(now - prev).count();
        prev = now;
        step(readInputs(), dt);
        if (watchdog_) watchdog_->kick();

        if (++ticks; now - rateT0 >= 1s) {
            std::lock_guard lock(statusMutex_);
            status_.loopHz = ticks / std::chrono::duration<double>(now - rateT0).count();
            ticks = 0;
            rateT0 = now;
        }
        next = std::max(next + period, Clock::now());  // never "catch up" after a stall
        std::this_thread::sleep_until(next);
    }
}

}  // namespace krbot::exec
