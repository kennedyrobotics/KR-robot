#pragma once
#include <string>
#include <fstream>
#include <chrono>
#include <thread>
#include <stdexcept>
#include <filesystem>

namespace fs = std::filesystem;

inline void write_file(const std::string& path, const std::string& val) {
    std::ofstream f(path);
    if (!f) throw std::runtime_error("Failed to open: " + path);
    f << val;
    if (!f) throw std::runtime_error("Failed to write to: " + path);
}

inline bool file_exists(const std::string& path) {
    std::error_code ec;
    return fs::exists(path, ec);
}

class SysfsPWM {
public:
    SysfsPWM(std::string pwmchip_path, int pwm_index)
    : chip_(std::move(pwmchip_path)), idx_(pwm_index) {
        export_if_needed();
    }
    void configure(uint32_t period_ns, uint32_t duty_ns, bool enable=true) {
        set_enable(false);
        write_file(pwm_path("period"), std::to_string(period_ns));
        write_file(pwm_path("duty_cycle"), std::to_string(duty_ns));
        set_enable(enable);
    }
    void set_duty(uint32_t duty_ns) {
        write_file(pwm_path("duty_cycle"), std::to_string(duty_ns));
    }
    void set_enable(bool en) {
        write_file(pwm_path("enable"), en ? "1" : "0");
    }
    uint32_t clamp_duty(uint32_t desired_ns, uint32_t period_ns) const {
        return desired_ns > period_ns ? period_ns : desired_ns;
    }
    std::string pwm_path(const std::string& leaf) const {
        return chip_ + "/pwm" + std::to_string(idx_) + "/" + leaf;
    }
private:
    void export_if_needed() {
        const std::string pwmN = chip_ + "/pwm" + std::to_string(idx_);
        if (!file_exists(pwmN)) {
            write_file(chip_ + "/export", std::to_string(idx_));
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
    }
    std::string chip_;
    int idx_;
};

class SysfsGPIO {
public:
    explicit SysfsGPIO(int gpio_num) : n_(gpio_num) {
        const std::string base = "/sys/class/gpio/gpio" + std::to_string(n_);
        if (!file_exists(base)) {
            write_file("/sys/class/gpio/export", std::to_string(n_));
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        write_file(base + "/direction", "out");
        path_ = base + "/value";
    }
    void set(int val) { write_file(path_, val ? "1" : "0"); }
private:
    int n_;
    std::string path_;
};

class MotorTB6612 {
public:
    MotorTB6612(int gpio_dirA,
                int gpio_dirB,
                const std::string& pwmchip_path,
                int pwm_index,
                uint32_t pwm_period_ns = 50000)
        : dirA_(gpio_dirA),
          dirB_(gpio_dirB),
          pwm_(pwmchip_path, pwm_index),
          period_ns_(pwm_period_ns)
    {
        pwm_.configure(period_ns_, 0, true);
        brake();
    }
    void setSpeed(float speed) {
        if (speed > 1.f)  speed = 1.f;
        if (speed < -1.f) speed = -1.f;
        if (speed > 0.001f) {
            dirA_.set(1);
            dirB_.set(0);
        } else if (speed < -0.001f) {
            dirA_.set(0);
            dirB_.set(1);
        } else {
            dirA_.set(0);
            dirB_.set(0);
        }
        const float duty_frac = std::min(1.f, std::abs(speed));
        const uint32_t duty = static_cast<uint32_t>(duty_frac * period_ns_);
        pwm_.set_duty(pwm_.clamp_duty(duty, period_ns_));
    }
    void brake() {
        dirA_.set(1);
        dirB_.set(1);
        pwm_.set_duty(0);
    }
    void coast() {
        dirA_.set(0);
        dirB_.set(0);
        pwm_.set_duty(0);
    }
private:
    SysfsGPIO dirA_;
    SysfsGPIO dirB_;
    SysfsPWM  pwm_;
    uint32_t  period_ns_;
};
