#include "driver/SerialPort.hpp"

#include <cerrno>
#include <fcntl.h>
#include <poll.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <stdexcept>
#include <system_error>
#include <termios.h>
#include <unistd.h>

namespace krbot::driver {

namespace {
[[noreturn]] void fail(const std::string& what) {
    throw std::system_error(errno, std::generic_category(), what);
}

speed_t toSpeed(unsigned baud) {
    switch (baud) {
        case 9600: return B9600;
        case 57600: return B57600;
        case 115200: return B115200;
        case 230400: return B230400;
        default: throw std::invalid_argument("unsupported baud rate");
    }
}
}  // namespace

SerialPort::~SerialPort() { close(); }

void SerialPort::open(const std::string& path, unsigned baud) {
    close();
    const int fd = ::open(path.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) fail("open " + path);
    if (::flock(fd, LOCK_EX | LOCK_NB) != 0) {
        const int e = errno;
        ::close(fd);
        errno = e;
        fail(path + " is in use by another process");
    }
    termios tio{};
    if (::tcgetattr(fd, &tio) != 0) {
        const int e = errno;
        ::close(fd);
        errno = e;
        fail("tcgetattr " + path);
    }
    ::cfmakeraw(&tio);
    tio.c_cflag |= CLOCAL | CREAD;
    tio.c_cflag &= ~(CSTOPB | CRTSCTS | PARENB);
    tio.c_cc[VMIN] = 0;
    tio.c_cc[VTIME] = 0;
    ::cfsetispeed(&tio, toSpeed(baud));
    ::cfsetospeed(&tio, toSpeed(baud));
    if (::tcsetattr(fd, TCSANOW, &tio) != 0) {
        const int e = errno;
        ::close(fd);
        errno = e;
        fail("tcsetattr " + path);
    }
    // Hold DTR/RTS low: CH340 auto-reset circuits would otherwise reset the STM32 on open.
    int lines = TIOCM_DTR | TIOCM_RTS;
    ::ioctl(fd, TIOCMBIC, &lines);
    ::tcflush(fd, TCIOFLUSH);
    fd_ = fd;
    path_ = path;
}

void SerialPort::close() {
    if (fd_ >= 0) {
        ::close(fd_);  // releases the flock
        fd_ = -1;
    }
}

void SerialPort::writeAll(const void* data, std::size_t len) {
    const auto* p = static_cast<const char*>(data);
    while (len > 0) {
        const ssize_t n = ::write(fd_, p, len);
        if (n < 0) {
            if (errno == EAGAIN || errno == EINTR) {
                pollfd pfd{fd_, POLLOUT, 0};
                ::poll(&pfd, 1, 50);
                continue;
            }
            fail("write " + path_);
        }
        p += n;
        len -= static_cast<std::size_t>(n);
    }
}

std::size_t SerialPort::read(void* buf, std::size_t len, std::chrono::milliseconds timeout) {
    pollfd pfd{fd_, POLLIN, 0};
    const int r = ::poll(&pfd, 1, static_cast<int>(timeout.count()));
    if (r < 0) {
        if (errno == EINTR) return 0;
        fail("poll " + path_);
    }
    if (r == 0) return 0;
    if (pfd.revents & (POLLERR | POLLHUP | POLLNVAL)) {
        errno = ENODEV;
        fail(path_ + " disconnected");
    }
    const ssize_t n = ::read(fd_, buf, len);
    if (n < 0) {
        if (errno == EAGAIN || errno == EINTR) return 0;
        fail("read " + path_);
    }
    return static_cast<std::size_t>(n);
}

}  // namespace krbot::driver
