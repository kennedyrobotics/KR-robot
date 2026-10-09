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
    BoardStatus boardStatus() const override {
        std::lock_guard lock(m_);
        return board_;
    }

    Channels last() const;
    std::size_t writes() const { return writes_; }
    std::size_t stops() const { return stops_; }
    void setHealthy(bool h) { healthy_ = h; }  // fault injection for tests
    void setBoard(const BoardStatus& b) {  // battery / liveness injection for tests (default: unsupported)
        std::lock_guard lock(m_);
        board_ = b;
    }

private:
    mutable std::mutex m_;
    Channels last_{};
    BoardStatus board_{};
    std::atomic<std::size_t> writes_{0};
    std::atomic<std::size_t> stops_{0};
    std::atomic<bool> healthy_{true};
};

}  // namespace krbot::driver
