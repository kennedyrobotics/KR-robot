#include "exec/Battery.hpp"

#include <algorithm>
#include <array>
#include <utility>

namespace krbot::exec {

const char* toString(BatteryState s) {
    switch (s) {
        case BatteryState::Unknown: return "unknown";
        case BatteryState::Ok: return "ok";
        case BatteryState::Warn: return "warn";
        case BatteryState::Low: return "low";
    }
    return "?";
}

double lipoPercent(double cellV) {
    // Common resting-voltage curve for a LiPo cell (V, %), linearly interpolated.
    static constexpr std::array<std::pair<double, double>, 12> kCurve{{
        {3.27, 0}, {3.61, 5}, {3.69, 10}, {3.73, 20}, {3.77, 30}, {3.80, 40},
        {3.84, 50}, {3.87, 60}, {3.95, 70}, {4.02, 80}, {4.11, 90}, {4.20, 100}}};
    if (cellV <= kCurve.front().first) return 0;
    if (cellV >= kCurve.back().first) return 100;
    for (std::size_t i = 1; i < kCurve.size(); ++i) {
        const auto [v1, p1] = kCurve[i];
        if (cellV <= v1) {
            const auto [v0, p0] = kCurve[i - 1];
            return p0 + (p1 - p0) * (cellV - v0) / (v1 - v0);
        }
    }
    return 100;
}

BatteryState BatteryMonitor::update(std::optional<double> volts, double ageS) {
    if (!volts || ageS < 0 || ageS > cfg_.staleS) {
        state_ = BatteryState::Unknown;
        armBlocked_ = false;
        return state_;
    }
    const double v = *volts, h = cfg_.hysteresisV;
    // Worsening is immediate; recovering needs the reading to clear the threshold by h.
    const bool low = v < cfg_.lowV || (state_ == BatteryState::Low && v < cfg_.lowV + h);
    const bool warn = v < cfg_.warnV || ((state_ == BatteryState::Warn || state_ == BatteryState::Low) && v < cfg_.warnV + h);
    state_ = low ? BatteryState::Low : warn ? BatteryState::Warn : BatteryState::Ok;
    armBlocked_ = cfg_.blockArmBelowV > 0 && v < cfg_.blockArmBelowV;
    return state_;
}

}  // namespace krbot::exec
