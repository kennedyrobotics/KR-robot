#include "driver/I2CDevice.hpp"

#include <cerrno>
#include <fcntl.h>
#include <linux/i2c-dev.h>
#include <sys/ioctl.h>
#include <system_error>
#include <unistd.h>
#include <vector>

namespace krbot::driver {

namespace {
[[noreturn]] void fail(const std::string& what) {
    throw std::system_error(errno, std::generic_category(), what);
}
}  // namespace

I2CDevice::I2CDevice(const std::string& bus, uint8_t address) : addr_(address), bus_(bus) {
    fd_ = ::open(bus.c_str(), O_RDWR | O_CLOEXEC);
    if (fd_ < 0) fail("open " + bus);
    if (::ioctl(fd_, I2C_SLAVE, static_cast<long>(address)) < 0) {
        const int e = errno;
        ::close(fd_);
        errno = e;
        fail("I2C_SLAVE on " + bus);
    }
}

I2CDevice::~I2CDevice() {
    if (fd_ >= 0) ::close(fd_);
}

void I2CDevice::write(std::span<const uint8_t> bytes) {
    if (::write(fd_, bytes.data(), bytes.size()) != static_cast<ssize_t>(bytes.size())) fail("i2c write " + bus_);
}

void I2CDevice::read(std::span<uint8_t> out) {
    if (::read(fd_, out.data(), out.size()) != static_cast<ssize_t>(out.size())) fail("i2c read " + bus_);
}

void I2CDevice::writeReg(uint8_t reg, std::span<const uint8_t> bytes) {
    std::vector<uint8_t> buf;
    buf.reserve(bytes.size() + 1);
    buf.push_back(reg);
    buf.insert(buf.end(), bytes.begin(), bytes.end());
    write(buf);
}

void I2CDevice::readReg(uint8_t reg, std::span<uint8_t> out) {
    const uint8_t r[1] = {reg};
    write(r);
    read(out);
}

}  // namespace krbot::driver
