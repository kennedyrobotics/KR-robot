#pragma once
// L1 motor interface - doc v3 §2, plus one extension (setAllChannels, see below).
// "Narrow and dumb by design: talk to hardware, expose typed C++ interfaces, no semantics."

#include <array>
#include <cstdint>

namespace krbot::driver {

class IMotorController {
public:
    static constexpr std::size_t kChannels = 4;
    using Channels = std::array<int16_t, kChannels>;

    // doc v3 §2. channel is 1-based (M1..M4, as silk-screened on the Yahboom board).
    // Units depend on the controller's mode: open-loop PWM counts, or closed-loop speed.
    virtual void setChannelSpeed(uint8_t channel, int16_t speed) = 0;

    // Extension to doc v3: the Yahboom board takes all four channels in one frame, so a
    // control tick writes once instead of four times (and left/right change atomically).
    virtual void setAllChannels(const Channels& speeds) = 0;

    virtual void stopAll() = 0;
    virtual bool isHealthy() const = 0;
    virtual ~IMotorController() = default;
};

}  // namespace krbot::driver
