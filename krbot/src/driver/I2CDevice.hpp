#pragma once
// RAII I2C device (raw ioctl I2C_SLAVE) — doc v3 §2/§8. Not used by the motor link (that moved
// to USB serial, see docs/system-design.md §Deviations) but kept for I2C sensors.
// BeagleY-AI GPIO/I2C is 3.3 V only.

#include <cstdint>
#include <span>
#include <string>

namespace krbot::driver {

class I2CDevice {
public:
    I2CDevice(const std::string& bus, uint8_t address);  // e.g. "/dev/i2c-1", 0x40; throws
    ~I2CDevice();
    I2CDevice(const I2CDevice&) = delete;
    I2CDevice& operator=(const I2CDevice&) = delete;

    void write(std::span<const uint8_t> bytes);
    void read(std::span<uint8_t> out);
    void writeReg(uint8_t reg, std::span<const uint8_t> bytes);
    void readReg(uint8_t reg, std::span<uint8_t> out);  // write reg pointer, then read

    uint8_t address() const { return addr_; }

private:
    int fd_ = -1;
    uint8_t addr_;
    std::string bus_;
};

}  // namespace krbot::driver
