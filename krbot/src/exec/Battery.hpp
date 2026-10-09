#pragma once
// Battery state from the motor board's voltage reading (Yahboom $read_vol#, 0.1 V resolution) - pure
// logic. The 2S LiPo has no low-voltage cutoff anywhere (notes/kr-robot-motor-control-and-joystick.md
// §4.1), so krbot warns, and refuses to ARM on a fresh reading below blockArmBelowV. It never stops a
// robot that is already driving: under load the pack sags, and a mid-drive stop is the bigger hazard.

#include <optional>

namespace krbot::exec {

enum class BatteryState { Unknown, Ok, Warn, Low };
const char* toString(BatteryState s);

struct BatteryConfig {
    int cells = 2;
    double warnV = 7.0;           // 3.5 V/cell: charge soon
    double lowV = 6.6;            // 3.3 V/cell: stop and charge
    double blockArmBelowV = 6.6;  // refuse ARM below this on a fresh reading (<= 0 disables)
    double staleS = 5.0;          // older readings count as Unknown and never block
    double hysteresisV = 0.1;     // one reading step: avoids flicker at a threshold
};

// Resting-voltage state of charge for one LiPo cell, 0..100 (approximate; reads low under load).
double lipoPercent(double cellV);

class BatteryMonitor {
public:
    explicit BatteryMonitor(BatteryConfig cfg = {}) : cfg_(cfg) {}

    // Feed the latest reading (nullopt = none) and its age. Returns the new state.
    BatteryState update(std::optional<double> volts, double ageS);
    BatteryState state() const { return state_; }
    bool armBlocked() const { return armBlocked_; }
    const BatteryConfig& config() const { return cfg_; }

private:
    BatteryConfig cfg_;
    BatteryState state_ = BatteryState::Unknown;
    bool armBlocked_ = false;
};

}  // namespace krbot::exec
