#include "driver/SimMotorController.hpp"

namespace krbot::driver {

void SimMotorController::setChannelSpeed(uint8_t channel, int16_t speed) {
    if (channel < 1 || channel > kChannels) return;
    std::lock_guard lock(m_);
    last_[channel - 1] = speed;
    ++writes_;
}

void SimMotorController::setAllChannels(const Channels& speeds) {
    std::lock_guard lock(m_);
    last_ = speeds;
    ++writes_;
}

void SimMotorController::stopAll() {
    std::lock_guard lock(m_);
    last_ = {};
    ++writes_;
    ++stops_;
}

IMotorController::Channels SimMotorController::last() const {
    std::lock_guard lock(m_);
    return last_;
}

}  // namespace krbot::driver
