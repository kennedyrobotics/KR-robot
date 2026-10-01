#include "exec/InputDriver.hpp"

#include "common/Log.hpp"

#include <algorithm>
#include <array>
#include <cerrno>
#include <dirent.h>
#include <fcntl.h>
#include <linux/input.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <unistd.h>

namespace krbot::exec {

static constexpr auto kTag = "L5.input";

namespace {
constexpr int kTriggerAxes[] = {ABS_Z, ABS_RZ, ABS_GAS, ABS_BRAKE};

bool testBit(const uint8_t* bits, int n) { return bits[n / 8] & (1u << (n % 8)); }
}  // namespace

bool AxisInfo::oneSided() const {
    // Triggers: xpad / hid-playstation report Z/RZ 0..255, BLE Xbox 0..1023. Xbox-over-BT without
    // xpadneo puts the right stick on Z/RZ at 0..65535 — the range check keeps that centred.
    const bool trigger = std::find(std::begin(kTriggerAxes), std::end(kTriggerAxes), code) != std::end(kTriggerAxes);
    return trigger && minimum >= 0 && maximum <= 1023;
}

float AxisInfo::normalise(int raw) const {
    if (maximum <= minimum) return 0.0f;
    if (oneSided())
        return std::clamp(static_cast<float>(raw - minimum) / static_cast<float>(maximum - minimum), 0.0f, 1.0f);
    const float centre = (static_cast<float>(minimum) + static_cast<float>(maximum)) / 2.0f;
    const float half = (static_cast<float>(maximum) - static_cast<float>(minimum)) / 2.0f;
    return std::clamp((static_cast<float>(raw) - centre) / half, -1.0f, 1.0f);
}

float GamepadState::axis(int code) const {
    const auto it = axes.find(code);
    return it == axes.end() ? 0.0f : it->second;
}

bool GamepadState::button(int code) const {
    const auto it = buttons.find(code);
    return it != buttons.end() && it->second;
}

InputDriver::~InputDriver() { close(); }

bool InputDriver::isGamepad(const std::string& path) {
    const int fd = ::open(path.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return false;
    uint8_t ev[(EV_MAX + 8) / 8] = {};
    uint8_t keys[(KEY_MAX + 8) / 8] = {};
    bool ok = ::ioctl(fd, EVIOCGBIT(0, sizeof ev), ev) >= 0 && testBit(ev, EV_KEY) && testBit(ev, EV_ABS) &&
              ::ioctl(fd, EVIOCGBIT(EV_KEY, sizeof keys), keys) >= 0 && testBit(keys, BTN_SOUTH);
    ::close(fd);
    return ok;
}

std::vector<std::string> InputDriver::findGamepads() {
    std::vector<std::pair<int, std::string>> found;
    if (DIR* d = ::opendir("/dev/input")) {
        while (const dirent* e = ::readdir(d)) {
            const std::string n = e->d_name;
            if (n.rfind("event", 0) != 0) continue;
            const std::string p = "/dev/input/" + n;
            if (isGamepad(p)) found.emplace_back(std::stoi(n.substr(5)), p);
        }
        ::closedir(d);
    }
    std::sort(found.begin(), found.end());
    std::vector<std::string> out;
    for (auto& [_, p] : found) out.push_back(p);
    return out;
}

bool InputDriver::open(const std::string& path) {
    close();
    std::string p = path;
    if (p.empty()) {
        const auto pads = findGamepads();
        if (pads.empty()) return false;
        p = pads.front();
    }
    const int fd = ::open(p.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return false;

    char name[256] = {};
    ::ioctl(fd, EVIOCGNAME(sizeof name), name);
    input_id id{};
    ::ioctl(fd, EVIOCGID, &id);
    ::ioctl(fd, EVIOCGRAB, 1);  // exclusive: stop the desktop treating the pad as mouse/keys

    uint8_t absBits[(ABS_MAX + 8) / 8] = {};
    ::ioctl(fd, EVIOCGBIT(EV_ABS, sizeof absBits), absBits);
    axisInfo_.clear();
    state_ = {};
    for (int code = 0; code <= ABS_MAX; ++code) {
        if (!testBit(absBits, code)) continue;
        input_absinfo ai{};
        if (::ioctl(fd, EVIOCGABS(code), &ai) < 0) continue;
        AxisInfo info{code, ai.minimum, ai.maximum, ai.flat};
        axisInfo_[code] = info;
        state_.axes[code] = info.normalise(ai.value);
    }
    fd_ = fd;
    path_ = p;
    name_ = name;
    bus_ = id.bustype;
    KLOG_INFO(kTag, "gamepad: {} ({}) bus={:#x} id={:04x}:{:04x}", name_, path_, id.bustype, id.vendor, id.product);
    return true;
}

void InputDriver::close() {
    if (fd_ >= 0) {
        ::close(fd_);
        fd_ = -1;
    }
}

InputDriver::PollResult InputDriver::poll(int timeoutMs) {
    if (fd_ < 0) return PollResult::LinkLost;
    pollfd pfd{fd_, POLLIN, 0};
    const int r = ::poll(&pfd, 1, timeoutMs);
    if (r < 0) return errno == EINTR ? PollResult::NoData : PollResult::LinkLost;
    if (r == 0) return PollResult::NoData;
    if (pfd.revents & (POLLERR | POLLHUP | POLLNVAL)) return PollResult::LinkLost;

    std::array<input_event, 64> evs{};
    const ssize_t n = ::read(fd_, evs.data(), sizeof evs);
    if (n < 0) return (errno == EAGAIN || errno == EINTR) ? PollResult::NoData : PollResult::LinkLost;
    if (n == 0) return PollResult::LinkLost;
    const auto count = static_cast<std::size_t>(n) / sizeof(input_event);
    for (std::size_t i = 0; i < count; ++i) {
        const auto& e = evs[i];
        if (e.type == EV_ABS) {
            if (const auto it = axisInfo_.find(e.code); it != axisInfo_.end())
                state_.axes[e.code] = it->second.normalise(e.value);
        } else if (e.type == EV_KEY) {
            state_.buttons[e.code] = e.value != 0;
        }
    }
    return PollResult::Updated;
}

}  // namespace krbot::exec
