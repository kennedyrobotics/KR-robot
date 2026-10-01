#pragma once
// Yahboom YB-ESF01 USART protocol — pure, no I/O (ported from krc/yahboom.py; same unit tests).
// ASCII frames "$cmd:args#" at 115200 8N1. Board reports: $MAll (total pulses),
// $MTEP (pulses per 10 ms), $MSPD (mm/s).
// UNVERIFIED (Phase 0): the "pwm"/"spd" keywords and the ±3600 PWM full scale.

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace krbot::driver::yahboom {

inline constexpr int kPwmFullScale = 3600;  // UNVERIFIED
inline constexpr unsigned kBaud = 115200;

struct MotorProfile {
    int mtype;
    std::optional<int> phase;            // reduction ratio
    std::optional<int> line;             // encoder lines
    std::optional<double> wheelDiaMm;
    std::optional<int> deadzone;
};

// Current 2-wire 33GB-520-18.7 (no encoder) -> Yahboom type 4, open-loop PWM.
inline const MotorProfile kProfile33GB520{4, std::nullopt, std::nullopt, std::nullopt, 1000};
// After the MD520Z30 encoder upgrade -> Yahboom type 1 defaults.
inline const MotorProfile kProfileMD520Z30{1, 30, 11, 67.0, 1900};

struct Telemetry {
    std::array<int32_t, 4> totalPulses{};
    std::array<int32_t, 4> pulses10ms{};
    std::array<double, 4> speedMmS{};
    bool any = false;  // at least one report parsed
};

std::string formatCommand(std::string_view name, const std::vector<int>& args);
// Separate name on purpose: an overload on double would capture formatCommand("x", {4}).
std::string formatCommandFloat(std::string_view name, double arg);  // e.g. wdiameter
std::vector<std::string> profileCommands(const MotorProfile& p);

// Incremental frame extractor: feed raw bytes, get complete frame bodies (without $ and #).
class FrameParser {
public:
    std::vector<std::string> feed(std::string_view bytes);
    std::size_t pending() const { return buf_.size(); }

private:
    std::string buf_;
    static constexpr std::size_t kMaxPending = 4096;  // drop garbage that never closes
};

// Applies $MAll / $MTEP / $MSPD to telem. Returns false if not a recognised/valid report.
bool parseReport(std::string_view frame, Telemetry& telem);

}  // namespace krbot::driver::yahboom
