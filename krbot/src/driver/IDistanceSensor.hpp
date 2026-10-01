#pragma once
// L1 distance-sensor interface — doc v3 §2. UltrasonicSensorDriver lands in Phase 0/1 once the
// transport (GPIO-timed via libgpiod vs I2C-native) is confirmed (doc v3 §11).

#include <optional>

namespace krbot::driver {

class IDistanceSensor {
public:
    virtual std::optional<float> readDistanceMeters() = 0;
    virtual ~IDistanceSensor() = default;
};

}  // namespace krbot::driver
