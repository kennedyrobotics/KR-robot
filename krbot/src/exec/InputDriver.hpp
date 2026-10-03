#pragma once
// L5 InputDriver - gamepad via raw Linux evdev (ported from krc/joystick.py). Works for the SN2403
// wired (xpad) and over BLE (hid-microsoft / uhid). Axis ranges come from EVIOCGABS so every mode
// normalises to -1..+1 (sticks) or 0..1 (triggers). Device loss -> PollResult::LinkLost, which the
// caller must treat as a safe-stop (doc v3 §6 / design note §6.2).

#include <cstdint>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <vector>

namespace krbot::exec {

struct AxisInfo {
    int code = 0;
    int minimum = 0;
    int maximum = 0;
    int flat = 0;
    bool oneSided() const;
    float normalise(int raw) const;
};

struct GamepadState {
    std::map<int, float> axes;     // linux ABS_* code -> normalised
    std::map<int, bool> buttons;   // linux BTN_* code -> pressed
    std::map<int, uint32_t> presses;  // code -> presses since open (monitor checklist: a tap between snapshots still counts)
    float axis(int code) const;
    bool button(int code) const;
};

class InputDriver {
public:
    enum class PollResult { NoData, Updated, LinkLost };

    InputDriver() = default;
    ~InputDriver();
    InputDriver(const InputDriver&) = delete;
    InputDriver& operator=(const InputDriver&) = delete;

    // Opens `path`, or the first gamepad found if empty. Returns false (no throw) if none.
    bool open(const std::string& path = {});
    void close();
    bool isOpen() const { return fd_ >= 0; }

    PollResult poll(int timeoutMs = 0);
    const GamepadState& state() const { return state_; }
    const std::string& name() const { return name_; }
    const std::string& path() const { return path_; }
    uint16_t busType() const { return bus_; }
    const std::string& deviceId() const { return id_; }  // "045e:02e0"
    const std::set<int>& keys() const { return keys_; }  // EV_KEY codes the device has, after remap
    bool remapped() const { return !buttonRemap_.empty(); }

    static std::vector<std::string> findGamepads();
    static bool isGamepad(const std::string& path);

private:
    int fd_ = -1;
    std::string path_, name_, id_;
    std::set<int> keys_;
    uint16_t bus_ = 0;
    std::map<int, AxisInfo> axisInfo_;
    std::map<int, int> buttonRemap_;   // raw EV_KEY code -> standard BTN_* (see InputDriver.cpp)
    GamepadState state_;
};

}  // namespace krbot::exec
