#include "driver/YahboomMotorControllerDriver.hpp"

#include "common/Log.hpp"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <stdexcept>
#include <system_error>

namespace krbot::driver {

using namespace std::chrono_literals;
static constexpr auto kTag = "L1.yahboom";
static constexpr auto kInitStepDelay = 100ms;  // Yahboom reference code waits ~100 ms per config step

YahboomMotorControllerDriver::YahboomMotorControllerDriver(Options opts) : opts_(std::move(opts)) {
    opts_.maxOutput = std::clamp(std::abs(opts_.maxOutput), 0, yahboom::kPwmFullScale);
}

YahboomMotorControllerDriver::~YahboomMotorControllerDriver() {
    if (port_.isOpen()) {
        try {
            stopAll();
        } catch (...) {
        }
    }
    running_ = false;
    if (rx_.joinable()) rx_.join();
    port_.close();
}

void YahboomMotorControllerDriver::open() {
    try {
        port_.open(opts_.port, yahboom::kBaud);
    } catch (const std::system_error& e) {
        if (opts_.fallbackPort.empty() || e.code() != std::errc::no_such_file_or_directory) throw;
        KLOG_WARN(kTag, "{} not found, trying {}", opts_.port, opts_.fallbackPort);
        port_.open(opts_.fallbackPort, yahboom::kBaud);
    }
    healthy_ = true;
    running_ = true;
    rx_ = std::thread([this] { rxLoop(); });
    KLOG_INFO(kTag, "opened {} @ {} (max output {})", port_.path(), yahboom::kBaud, opts_.maxOutput);
}

void YahboomMotorControllerDriver::configure(const yahboom::MotorProfile& profile) {
    for (const auto& cmd : yahboom::profileCommands(profile)) {
        writeFrame(cmd);
        std::this_thread::sleep_for(kInitStepDelay);
    }
}

void YahboomMotorControllerDriver::setUpload(bool total, bool per10ms, bool speed) {
    writeFrame(yahboom::formatCommand("upload", {int(total), int(per10ms), int(speed)}));
    std::this_thread::sleep_for(kInitStepDelay);
}

void YahboomMotorControllerDriver::sendRaw(const std::string& frame) { writeFrame(frame); }

int16_t YahboomMotorControllerDriver::clamp(int16_t v) const {
    return static_cast<int16_t>(std::clamp<int>(v, -opts_.maxOutput, opts_.maxOutput));
}

void YahboomMotorControllerDriver::setChannelSpeed(uint8_t channel, int16_t speed) {
    if (channel < 1 || channel > kChannels) return;
    Channels next;
    {
        std::lock_guard lock(txMutex_);
        next = last_;
    }
    next[channel - 1] = speed;
    setAllChannels(next);
}

void YahboomMotorControllerDriver::setAllChannels(const Channels& speeds) {
    Channels c;
    for (std::size_t i = 0; i < kChannels; ++i) c[i] = clamp(speeds[i]);
    const auto& kw = opts_.mode == Mode::Pwm ? opts_.pwmKeyword : opts_.speedKeyword;
    writeFrame(yahboom::formatCommand(kw, {c[0], c[1], c[2], c[3]}));
    std::lock_guard lock(txMutex_);
    last_ = c;
}

void YahboomMotorControllerDriver::stopAll() { setAllChannels(Channels{}); }

bool YahboomMotorControllerDriver::isHealthy() const { return healthy_ && port_.isOpen(); }

void YahboomMotorControllerDriver::writeFrame(const std::string& frame) {
    std::lock_guard lock(txMutex_);
    if (!port_.isOpen()) throw std::runtime_error("motor port not open");
    try {
        port_.writeAll(frame.data(), frame.size());
        KLOG_DEBUG(kTag, "TX {}", frame);
    } catch (const std::system_error& e) {
        healthy_ = false;
        {
            std::lock_guard rl(rxMutex_);
            lastError_ = e.what();
        }
        throw;
    }
}

void YahboomMotorControllerDriver::rxLoop() {
    yahboom::FrameParser parser;
    char buf[256];
    while (running_) {
        std::size_t n = 0;
        try {
            n = port_.read(buf, sizeof buf, 50ms);
        } catch (const std::system_error& e) {
            KLOG_ERROR(kTag, "serial read failed: {}", e.what());
            healthy_ = false;
            std::lock_guard lock(rxMutex_);
            lastError_ = e.what();
            return;
        }
        if (n == 0) continue;
        for (auto& f : parser.feed(std::string_view(buf, n))) {
            KLOG_DEBUG(kTag, "RX ${}#", f);
            std::lock_guard lock(rxMutex_);
            if (!yahboom::parseReport(f, telem_)) {
                other_.push_back(std::move(f));
                if (other_.size() > 50) other_.erase(other_.begin());
            }
        }
    }
}

yahboom::Telemetry YahboomMotorControllerDriver::telemetry() const {
    std::lock_guard lock(rxMutex_);
    return telem_;
}

std::vector<std::string> YahboomMotorControllerDriver::unrecognisedFrames() const {
    std::lock_guard lock(rxMutex_);
    return other_;
}

std::string YahboomMotorControllerDriver::lastError() const {
    std::lock_guard lock(rxMutex_);
    return lastError_;
}

}  // namespace krbot::driver
