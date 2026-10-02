#pragma once
// Teleop safety state machine + skid-steer mixing - pure logic, ported 1:1 from krc/drive.py
// (same unit tests). L5's direct manual path (doc v3 §6).
//
//  DISARMED at start-up and after link loss. ARM: START with deadman released + sticks centred.
//  DRIVE only while deadman (LB) held; release -> instant zero. ESTOP (B/HOME, GUI, watchdog,
//  motor fault) latches, survives link loss, and is cleared only by re-arming with START.

#include <array>
#include <string>

namespace krbot::exec {

enum class TeleopMode { Disarmed, Armed, Estop };
const char* toString(TeleopMode m);

float deadband(float v, float band);
float expo(float v, float k);
std::pair<float, float> arcadeMix(float throttle, float steer);

struct TeleopConfig {
    int maxOutput = 1800;          // bench default ~50 % of the (unverified) 3600 full scale
    float stickDeadband = 0.08f;
    float expo = 0.3f;
    float turnScale = 0.6f;        // pivot turns load the AT8236 drivers hard
    float slewPerS = 3.0f;
    bool tank = false;
    bool invertLeft = false;
    bool invertRight = true;
    int leftChannel = 1;           // M1
    int rightChannel = 2;          // M2
};

struct TeleopInputs {
    float lx = 0, ly = 0, rx = 0, ry = 0;  // evdev convention: stick up = negative Y
    bool deadman = false;                  // LB
    bool arm = false;                      // START
    bool estop = false;                    // B or HOME
    bool linkOk = true;
};

struct TeleopOutput {
    int left = 0;
    int right = 0;
};

class TeleopController {
public:
    explicit TeleopController(TeleopConfig cfg = {}) : cfg_(cfg) {}

    TeleopOutput update(const TeleopInputs& in, float dt);
    void forceEstop(std::string reason);
    std::array<int16_t, 4> channels(TeleopOutput out) const;

    TeleopMode mode() const { return mode_; }
    const std::string& reason() const { return reason_; }
    const TeleopConfig& config() const { return cfg_; }

private:
    bool sticksCentred(const TeleopInputs& in) const;

    TeleopConfig cfg_;
    TeleopMode mode_ = TeleopMode::Disarmed;
    std::string reason_ = "start-up";
    float left_ = 0, right_ = 0;
    bool prevArm_ = false;
};

}  // namespace krbot::exec
