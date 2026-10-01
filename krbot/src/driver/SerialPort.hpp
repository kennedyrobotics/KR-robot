#pragma once
// RAII raw serial port (termios). Exclusive: flock(LOCK_EX) — the same lock pyserial's
// `exclusive=True` takes, so the C++ stack, the Python GUI and tools/teleop.py can never
// drive the motor board at the same time.

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <string>

namespace krbot::driver {

class SerialPort {
public:
    SerialPort() = default;
    ~SerialPort();
    SerialPort(const SerialPort&) = delete;
    SerialPort& operator=(const SerialPort&) = delete;

    // Throws std::system_error on failure (ENOENT, EBUSY if another process holds it, ...).
    void open(const std::string& path, unsigned baud = 115200);
    void close();
    bool isOpen() const { return fd_ >= 0; }
    const std::string& path() const { return path_; }

    // Writes all bytes; throws std::system_error on error.
    void writeAll(const void* data, std::size_t len);
    // Waits up to timeout; returns bytes read (0 on timeout). Throws on error / device gone.
    std::size_t read(void* buf, std::size_t len, std::chrono::milliseconds timeout);

private:
    int fd_ = -1;
    std::string path_;
};

}  // namespace krbot::driver
