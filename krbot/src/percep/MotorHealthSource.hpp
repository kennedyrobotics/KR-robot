#pragma once
// L2 source: motor-board link health (and, after the encoder upgrade, wheel speed telemetry).

#include "driver/IMotorController.hpp"
#include "percep/Observation.hpp"

namespace krbot::percep {

class MotorHealthSource final : public ISensorSource {
public:
    explicit MotorHealthSource(const driver::IMotorController& motors) : motors_(motors) {}
    std::vector<Observation> poll() override;

private:
    const driver::IMotorController& motors_;
};

}  // namespace krbot::percep
