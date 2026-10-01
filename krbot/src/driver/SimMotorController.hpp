#pragma once
// In-memory IMotorController for --dry-run and tests. Records every write.

#include "driver/IMotorController.hpp"

#include <atomic>
#include <mutex>

namespace krbot::driver {

class SimMotorController final : public IMotorController {
public:
    void setChannelSpeed(uint8_t channel, int16_t speed) override;
    void setAllChannels(const Channels& speeds) override;
    void stopAll() override;
    bool isHealthy() const override { return healthy_; }

    Channels last() const;
    std::size_t writes() const { return writes_; }
    std::size_t stops() const { return stops_; }
    void setHealthy(bool h) { healthy_ = h; }  // fault injection for tests

private:
    mutable std::mutex m_;
    Channels last_{};
    std::atomic<std::size_t> writes_{0};
    std::atomic<std::size_t> stops_{0};
    std::atomic<bool> healthy_{true};
};

}  // namespace krbot::driver
