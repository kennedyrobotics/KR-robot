#pragma once
// YahboomMotorControllerDriver : IMotorController - doc v3 §2, over USB serial (CH340K) instead
// of I2C (design note §2.3). Thread-safe: setAllChannels/stopAll may be called from the control
// thread and the watchdog thread concurrently. A background RX thread parses board reports.

#include "driver/IMotorController.hpp"
#include "driver/SerialPort.hpp"
#include "driver/YahboomProtocol.hpp"

#include <atomic>
#include <chrono>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace krbot::driver {

class YahboomMotorControllerDriver final : public IMotorController {
public:
    enum class Mode { Pwm, Speed };  // Pwm = open loop (no encoders); Speed = closed loop

    struct Options {
        std::string port = "/dev/ttyAMA0";          // header UART (wiring-manual §5.1b)
        std::string fallbackPort = "/dev/krc-motor";  // USB-C link
        std::string pwmKeyword = "pwm";    // confirmed (Yahboom control command doc)
        std::string speedKeyword = "spd";  // confirmed (Yahboom control command doc)
        int batteryPollMs = 1000;          // send $read_vol# this often from the RX thread (0 = never)
        int maxOutput = 1800;              // clamp, PWM counts (bench default ~50 %)
        Mode mode = Mode::Pwm;
    };

    explicit YahboomMotorControllerDriver(Options opts);
    ~YahboomMotorControllerDriver() override;

    void open();  // throws std::system_error
    void configure(const yahboom::MotorProfile& profile);
    void setUpload(bool total, bool per10ms, bool speed);
    void sendRaw(const std::string& frame);

    // IMotorController
    void setChannelSpeed(uint8_t channel, int16_t speed) override;
    void setAllChannels(const Channels& speeds) override;
    void stopAll() override;
    bool isHealthy() const override;
    BoardStatus boardStatus() const override;

    yahboom::Telemetry telemetry() const;
    std::vector<std::string> unrecognisedFrames() const;
    std::string port() const { return port_.path(); }
    std::string lastError() const;

private:
    void writeFrame(const std::string& frame);
    void rxLoop();
    int16_t clamp(int16_t v) const;

    Options opts_;
    SerialPort port_;
    mutable std::mutex txMutex_;
    mutable std::mutex rxMutex_;
    Channels last_{};
    yahboom::Telemetry telem_;
    std::vector<std::string> other_;
    std::chrono::steady_clock::time_point lastRx_{}, batteryAt_{};  // guarded by rxMutex_
    uint64_t replies_ = 0;                                          // guarded by rxMutex_
    std::string lastError_;
    std::atomic<bool> running_{false};
    std::atomic<bool> healthy_{false};
    std::thread rx_;
};

}  // namespace krbot::driver
