// ManualDrive.step(): the direct L5->L1 path with a simulated motor controller.
#include "driver/SimMotorController.hpp"
#include "exec/ManualDrive.hpp"

#include <gtest/gtest.h>

using namespace krbot;
using exec::TeleopInputs;
using exec::TeleopMode;

namespace {
constexpr float kDt = 0.02f;

exec::ManualDrive::Options opts() {
    exec::ManualDrive::Options o;
    o.teleop.maxOutput = 1000;
    o.teleop.expo = 0;
    o.teleop.invertRight = true;
    return o;
}

TeleopInputs drive() {
    TeleopInputs i;
    i.ly = -1;
    i.deadman = true;
    return i;
}

void arm(exec::ManualDrive& md) {
    TeleopInputs a;
    a.arm = true;
    md.step(a, kDt);
    md.step({}, kDt);
}
}  // namespace

TEST(ManualDrive, WritesMixedChannelsToMotors) {
    driver::SimMotorController m;
    exec::ManualDrive md(m, nullptr, nullptr, opts());
    arm(md);
    for (int i = 0; i < 100; ++i) md.step(drive(), kDt);
    EXPECT_EQ(m.last(), (driver::IMotorController::Channels{1000, -1000, 0, 0}));
}

TEST(ManualDrive, RequestEstopFromAnotherThreadZerosNextTick) {
    driver::SimMotorController m;
    exec::ManualDrive md(m, nullptr, nullptr, opts());
    arm(md);
    for (int i = 0; i < 100; ++i) md.step(drive(), kDt);
    md.requestEstop("test");
    md.step(drive(), kDt);
    EXPECT_EQ(md.status().mode, TeleopMode::Estop);
    EXPECT_EQ(m.last(), (driver::IMotorController::Channels{}));
}

TEST(ManualDrive, UnhealthyMotorsLatchEstopAndBlockArming) {
    driver::SimMotorController m;
    exec::ManualDrive md(m, nullptr, nullptr, opts());
    arm(md);
    m.setHealthy(false);
    md.step(drive(), kDt);
    EXPECT_EQ(md.status().mode, TeleopMode::Estop);
    arm(md);  // START pressed while the board is still down
    EXPECT_EQ(md.status().mode, TeleopMode::Estop);
    EXPECT_FALSE(md.status().motorsHealthy);
}
