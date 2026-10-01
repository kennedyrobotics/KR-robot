#pragma once
// DriverRegistry — doc v3 §2: owns all L1 driver instances and is the ONLY place concrete
// driver types are named. Everything above L1 sees interfaces.

#include "common/Config.hpp"
#include "driver/IDistanceSensor.hpp"
#include "driver/IMotorController.hpp"

#include <map>
#include <memory>
#include <string>

namespace krbot::driver {

class DriverRegistry {
public:
    // Builds drivers from config. motor.driver = yahboom | sim (dry-run).
    // A Yahboom open failure is logged and leaves the motor driver unhealthy-but-present
    // (a SimMotorController marked unhealthy), so the stack still starts and reports it.
    static std::unique_ptr<DriverRegistry> fromConfig(const common::Config& cfg);

    IMotorController& motors() { return *motors_; }
    const std::string& motorDescription() const { return motorDesc_; }

    void addDistanceSensor(const std::string& name, std::unique_ptr<IDistanceSensor> s);
    IDistanceSensor* distanceSensor(const std::string& name);

private:
    std::unique_ptr<IMotorController> motors_;
    std::string motorDesc_;
    std::map<std::string, std::unique_ptr<IDistanceSensor>> distance_;
};

}  // namespace krbot::driver
