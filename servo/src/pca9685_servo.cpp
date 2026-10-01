#include <fcntl.h>
#include <linux/i2c-dev.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <exception>
#include <iostream>
#include <stdexcept>
#include <string>

// ===== PCA9685 Register Map =====
constexpr uint8_t PCA9685_MODE1      = 0x00;
constexpr uint8_t PCA9685_MODE2      = 0x01;
constexpr uint8_t PCA9685_SUBADR1    = 0x02;
constexpr uint8_t PCA9685_SUBADR2    = 0x03;
constexpr uint8_t PCA9685_SUBADR3    = 0x04;
constexpr uint8_t PCA9685_PRESCALE   = 0xFE;
constexpr uint8_t LED0_ON_L          = 0x06;
constexpr uint8_t ALL_LED_ON_L       = 0xFA;
constexpr uint8_t ALL_LED_ON_H       = 0xFB;
constexpr uint8_t ALL_LED_OFF_L      = 0xFC;
constexpr uint8_t ALL_LED_OFF_H      = 0xFD;

// MODE1 bits
constexpr uint8_t MODE1_RESTART = 0x80;
constexpr uint8_t MODE1_EXTCLK  = 0x40; // not used here
constexpr uint8_t MODE1_AI      = 0x20; // auto-increment
constexpr uint8_t MODE1_SLEEP   = 0x10;
constexpr uint8_t MODE1_SUB1    = 0x08;
constexpr uint8_t MODE1_SUB2    = 0x04;
constexpr uint8_t MODE1_SUB3    = 0x02;
constexpr uint8_t MODE1_ALLCALL = 0x01;

// MODE2 bits
constexpr uint8_t MODE2_INVRT   = 0x10;
constexpr uint8_t MODE2_OCH     = 0x08;
constexpr uint8_t MODE2_OUTDRV  = 0x04; // totem-pole
constexpr uint8_t MODE2_OUTNE1  = 0x01;

constexpr uint16_t PCA9685_COUNTER_MAX = 4096; // 12-bit

// Adjust if your Beagley-AI routes I2C differently (often /dev/i2c-1 for the 40-pin header).
static const char* I2C_DEV_DEFAULT = "/dev/i2c-1";

struct ServoTiming {
    float freq_hz = 50.0f;     // 50 Hz
    float min_us  = 500.0f;    // 0°
    float max_us  = 2500.0f;   // 180°
    float min_deg = 0.0f;
    float max_deg = 180.0f;
};

class I2CDevice {
public:
    I2CDevice(const std::string& dev, uint8_t addr) : _dev(dev), _addr(addr) {
        _fd = open(_dev.c_str(), O_RDWR);
        if (_fd < 0) {
            throw std::runtime_error("Failed to open " + _dev + ": " + std::string(std::strerror(errno)));
        }
        if (ioctl(_fd, I2C_SLAVE, _addr) < 0) {
            ::close(_fd);
            throw std::runtime_error("Failed to set I2C address 0x" + hex(_addr) + ": " + std::string(std::strerror(errno)));
        }
    }
    ~I2CDevice() {
        if (_fd >= 0) ::close(_fd);
    }

    void write8(uint8_t reg, uint8_t val) {
        uint8_t buf[2] = {reg, val};
        if (::write(_fd, buf, 2) != 2) {
            throw std::runtime_error("I2C write8 failed at reg 0x" + hex(reg) + ": " + std::string(std::strerror(errno)));
        }
    }

    uint8_t read8(uint8_t reg) {
        if (::write(_fd, &reg, 1) != 1) {
            throw std::runtime_error("I2C read8 (addr phase) failed at reg 0x" + hex(reg) + ": " + std::string(std::strerror(errno)));
        }
        uint8_t val = 0;
        if (::read(_fd, &val, 1) != 1) {
            throw std::runtime_error("I2C read8 (data phase) failed at reg 0x" + hex(reg) + ": " + std::string(std::strerror(errno)));
        }
        return val;
    }

    void writeBlock(uint8_t reg, const uint8_t* data, size_t len) {
        if (len > 32) throw std::runtime_error("writeBlock len too large");
        uint8_t buf[1 + 32];
        buf[0] = reg;
        std::memcpy(&buf[1], data, len);
        if (::write(_fd, buf, 1 + len) != static_cast<ssize_t>(1 + len)) {
            throw std::runtime_error("I2C writeBlock failed at reg 0x" + hex(reg) + ": " + std::string(std::strerror(errno)));
        }
    }

private:
    std::string _dev;
    int _fd = -1;
    uint8_t _addr;

    static std::string hex(uint32_t v) {
        char b[9];
        std::snprintf(b, sizeof(b), "%02X", v);
        return std::string(b);
    }
};

class PCA9685 {
public:
    PCA9685(const std::string& i2c_dev, uint8_t addr, float osc_hz = 25'000'000.0f)
        : _i2c(i2c_dev, addr), _oscillator_hz(osc_hz) {}

    void begin(const ServoTiming& timing = {}) {
        _i2c.write8(PCA9685_MODE1, MODE1_AI | MODE1_ALLCALL);
        usleep(5000);
        _i2c.write8(PCA9685_MODE2, MODE2_OUTDRV);
        setPWMFreq(timing.freq_hz);
        setAllPWM(0, 0);
    }

    void setPWMFreq(float freq_hz) {
        if (freq_hz < 24.0f) freq_hz = 24.0f;
        if (freq_hz > 1526.0f) freq_hz = 1526.0f;
        float prescale_f = (_oscillator_hz / (PCA9685_COUNTER_MAX * freq_hz)) - 1.0f;
        uint8_t prescale = static_cast<uint8_t>(std::floor(prescale_f + 0.5f));

        uint8_t oldmode = _i2c.read8(PCA9685_MODE1);
        uint8_t sleepmode = (oldmode & ~MODE1_RESTART) | MODE1_SLEEP;

        _i2c.write8(PCA9685_MODE1, sleepmode);
        _i2c.write8(PCA9685_PRESCALE, prescale);
        _i2c.write8(PCA9685_MODE1, oldmode);
        usleep(5000);
        _i2c.write8(PCA9685_MODE1, oldmode | MODE1_RESTART | MODE1_AI);
    }

    void setPWM(uint8_t channel, uint16_t on_count, uint16_t off_count) {
        if (channel > 15) throw std::invalid_argument("channel must be 0..15");
        if (on_count >= PCA9685_COUNTER_MAX || off_count >= PCA9685_COUNTER_MAX)
            throw std::invalid_argument("on/off must be < 4096");
        uint8_t reg = LED0_ON_L + 4 * channel;
        uint8_t data[4] = {
            static_cast<uint8_t>(on_count & 0xFF),
            static_cast<uint8_t>((on_count >> 8) & 0x0F),
            static_cast<uint8_t>(off_count & 0xFF),
            static_cast<uint8_t>((off_count >> 8) & 0x0F)
        };
        _i2c.writeBlock(reg, data, 4);
    }

    void setAllPWM(uint16_t on_count, uint16_t off_count) {
        uint8_t data[4] = {
            static_cast<uint8_t>(on_count & 0xFF),
            static_cast<uint8_t>((on_count >> 8) & 0x0F),
            static_cast<uint8_t>(off_count & 0xFF),
            static_cast<uint8_t>((off_count >> 8) & 0x0F)
        };
        _i2c.writeBlock(ALL_LED_ON_L, data, 4);
    }

    void setServoAngle(uint8_t channel, float degrees, const ServoTiming& timing = {}) {
        float clamped_deg = std::max(timing.min_deg, std::min(degrees, timing.max_deg));
        float span_deg = (timing.max_deg - timing.min_deg);
        float span_us  = (timing.max_us  - timing.min_us);
        float us = timing.min_us + ((clamped_deg - timing.min_deg) / span_deg) * span_us;
        setServoPulseUs(channel, us, timing.freq_hz);
    }

    void setServoPulseUs(uint8_t channel, float pulse_us, float freq_hz = 50.0f) {
        float period_us = 1'000'000.0f / freq_hz;
        if (pulse_us < 0) pulse_us = 0;
        if (pulse_us > period_us) pulse_us = period_us;
        float ticks_f = (pulse_us / period_us) * PCA9685_COUNTER_MAX;
        uint16_t off = static_cast<uint16_t>(std::round(ticks_f));
        if (off >= PCA9685_COUNTER_MAX) off = PCA9685_COUNTER_MAX - 1;
        setPWM(channel, 0, off);
    }

private:
    I2CDevice _i2c;
    float _oscillator_hz;
};

int main(int argc, char** argv) {
    std::string i2cdev = I2C_DEV_DEFAULT;
    uint8_t addr = 0x40;
    int channel = 0;
    float angle = 90.0f;

    if (argc > 1) i2cdev = argv[1];
    if (argc > 2) addr = static_cast<uint8_t>(std::strtol(argv[2], nullptr, 0));
    if (argc > 3) channel = std::stoi(argv[3]);
    if (argc > 4) angle = std::stof(argv[4]);

    try {
        ServoTiming timing;
        PCA9685 pca(i2cdev, addr);
        pca.begin(timing);

        std::cout << "Setting channel " << channel << " to " << angle << " degrees...\n";
        pca.setServoAngle(static_cast<uint8_t>(channel), angle, timing);
        std::cout << "Done.\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << "\n";
        return 1;
    }
}
