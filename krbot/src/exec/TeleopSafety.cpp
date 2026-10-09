#include "exec/TeleopSafety.hpp"

#include <algorithm>
#include <cmath>

namespace krbot::exec {

const char* toString(TeleopMode m) {
    switch (m) {
        case TeleopMode::Disarmed: return "DISARMED";
        case TeleopMode::Armed: return "ARMED";
        case TeleopMode::Estop: return "ESTOP";
    }
    return "?";
}

float deadband(float v, float band) {
    if (std::fabs(v) <= band) return 0.0f;
    return std::copysign((std::fabs(v) - band) / (1.0f - band), v);
}

float expo(float v, float k) { return (1.0f - k) * v + k * v * v * v; }

std::pair<float, float> arcadeMix(float throttle, float steer) {
    const float l = throttle + steer;
    const float r = throttle - steer;
    const float m = std::max({1.0f, std::fabs(l), std::fabs(r)});
    return {l / m, r / m};
}

bool TeleopController::sticksCentred(const TeleopInputs& in) const {
    const float b = cfg_.stickDeadband;
    return std::fabs(in.lx) <= b && std::fabs(in.ly) <= b && std::fabs(in.rx) <= b && std::fabs(in.ry) <= b;
}

bool TeleopController::deadmanHeld(const TeleopInputs& in, float dt) {
    // Presses count at once; a release only after it has lasted deadmanReleaseMs, so a single
    // all-buttons-up report from the pad can't stop the robot. Link loss bypasses this.
    if (!in.linkOk) {
        deadmanLatched_ = false;
    } else if (in.deadman) {
        deadmanLatched_ = true;
        deadmanOffS_ = 0;
    } else if (deadmanLatched_) {
        deadmanOffS_ += dt;
        if (deadmanOffS_ + 1e-4f >= static_cast<float>(cfg_.deadmanReleaseMs) / 1000.0f) deadmanLatched_ = false;
    }
    return deadmanLatched_;
}

void TeleopController::forceEstop(std::string reason) {
    mode_ = TeleopMode::Estop;
    reason_ = std::move(reason);
    left_ = right_ = 0;
}

TeleopOutput TeleopController::update(const TeleopInputs& in, float dt) {
    const bool armEdge = in.arm && !prevArm_;
    prevArm_ = in.arm;
    const bool deadman = deadmanHeld(in, dt);

    if (!in.linkOk) {
        // ESTOP is stricter than DISARMED, so it stays latched through link loss
        if (mode_ == TeleopMode::Armed) {
            mode_ = TeleopMode::Disarmed;
            reason_ = "link lost";
        }
    } else if (in.estop) {
        mode_ = TeleopMode::Estop;
        reason_ = "e-stop pressed";
    } else if (armEdge && mode_ != TeleopMode::Armed) {
        if (deadman || !sticksCentred(in)) {
            reason_ = "arm refused: release LB and centre sticks";
        } else if (in.armBlocked) {
            reason_ = "arm refused: battery low";
        } else {
            mode_ = TeleopMode::Armed;
            reason_ = "armed";
        }
    }

    float tl = 0, tr = 0;
    if (mode_ == TeleopMode::Armed && deadman) {
        const auto& c = cfg_;
        if (c.tank) {
            tl = expo(deadband(-in.ly, c.stickDeadband), c.expo);
            tr = expo(deadband(-in.ry, c.stickDeadband), c.expo);
        } else {
            const float thr = expo(deadband(-in.ly, c.stickDeadband), c.expo);
            const float steer = expo(deadband(in.rx, c.stickDeadband), c.expo) * c.turnScale;
            std::tie(tl, tr) = arcadeMix(thr, steer);
        }
    }

    if (tl == 0 && tr == 0) {
        left_ = right_ = 0;  // stopping is never rate-limited
    } else {
        const float step = cfg_.slewPerS * dt;
        left_ += std::clamp(tl - left_, -step, step);
        right_ += std::clamp(tr - right_, -step, step);
    }

    const float l = cfg_.invertLeft ? -left_ : left_;
    const float r = cfg_.invertRight ? -right_ : right_;
    return {static_cast<int>(std::lround(l * static_cast<float>(cfg_.maxOutput))),
            static_cast<int>(std::lround(r * static_cast<float>(cfg_.maxOutput)))};
}

std::array<int16_t, 4> TeleopController::channels(TeleopOutput out) const {
    std::array<int16_t, 4> ch{};
    if (cfg_.leftChannel >= 1 && cfg_.leftChannel <= 4) ch[cfg_.leftChannel - 1] = static_cast<int16_t>(out.left);
    if (cfg_.rightChannel >= 1 && cfg_.rightChannel <= 4) ch[cfg_.rightChannel - 1] = static_cast<int16_t>(out.right);
    return ch;
}

}  // namespace krbot::exec
