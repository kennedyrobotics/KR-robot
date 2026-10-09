#include "driver/DriverRegistry.hpp"

#include "common/Log.hpp"
#include "driver/SimMotorController.hpp"
#include "driver/YahboomMotorControllerDriver.hpp"

#include <exception>

namespace krbot::driver {

static constexpr auto kTag = "L1.registry";

std::unique_ptr<DriverRegistry> DriverRegistry::fromConfig(const common::Config& cfg) {
    auto reg = std::make_unique<DriverRegistry>();
    const auto kind = cfg.getString("motor.driver", "yahboom");

    if (kind == "yahboom") {
        YahboomMotorControllerDriver::Options o;
        o.port = cfg.getString("motor.port", o.port);
        o.fallbackPort = cfg.getString("motor.fallback_port", o.fallbackPort);
        o.pwmKeyword = cfg.getString("motor.pwm_keyword", o.pwmKeyword);
        o.speedKeyword = cfg.getString("motor.speed_keyword", o.speedKeyword);
        o.batteryPollMs = static_cast<int>(cfg.getInt("motor.battery_poll_ms", o.batteryPollMs));
        o.maxOutput = static_cast<int>(cfg.getInt("motor.max_output", o.maxOutput));
        o.mode = cfg.getString("motor.mode", "pwm") == "speed" ? YahboomMotorControllerDriver::Mode::Speed
                                                              : YahboomMotorControllerDriver::Mode::Pwm;
        auto drv = std::make_unique<YahboomMotorControllerDriver>(o);
        try {
            drv->open();
            const auto profile = cfg.getString("motor.profile", "33gb520");
            if (cfg.getBool("motor.init_on_open", true))
                drv->configure(profile == "md520" ? yahboom::kProfileMD520Z30 : yahboom::kProfile33GB520);
            drv->stopAll();
            reg->motorDesc_ = "yahboom " + drv->port();
            reg->motors_ = std::move(drv);
        } catch (const std::exception& e) {
            KLOG_ERROR(kTag, "motor board unavailable: {} - running with an UNHEALTHY placeholder", e.what());
            auto sim = std::make_unique<SimMotorController>();
            sim->setHealthy(false);
            reg->motorDesc_ = std::string("unavailable (") + e.what() + ")";
            reg->motors_ = std::move(sim);
        }
    } else {
        reg->motors_ = std::make_unique<SimMotorController>();
        reg->motorDesc_ = "sim (dry-run)";
    }
    KLOG_INFO(kTag, "motors: {}", reg->motorDesc_);
    return reg;
}

void DriverRegistry::addDistanceSensor(const std::string& name, std::unique_ptr<IDistanceSensor> s) {
    distance_[name] = std::move(s);
}

IDistanceSensor* DriverRegistry::distanceSensor(const std::string& name) {
    const auto it = distance_.find(name);
    return it == distance_.end() ? nullptr : it->second.get();
}

}  // namespace krbot::driver
