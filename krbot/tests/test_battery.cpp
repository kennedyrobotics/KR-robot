// Battery voltage from the Yahboom board ($read_vol# -> $Battery:7.40V#) and the logic built on it.
#include "driver/SimMotorController.hpp"
#include "driver/YahboomProtocol.hpp"
#include "exec/Battery.hpp"
#include "exec/ManualDrive.hpp"

#include <gtest/gtest.h>

using namespace krbot;
using exec::BatteryMonitor;
using exec::BatteryState;

TEST(YahboomBattery, ParsesReply) {
    driver::yahboom::Telemetry t;
    EXPECT_TRUE(driver::yahboom::parseReport("Battery:7.40V", t));
    ASSERT_TRUE(t.batteryV);
    EXPECT_DOUBLE_EQ(*t.batteryV, 7.40);
    EXPECT_TRUE(driver::yahboom::parseReport("Battery:6.7V", t));  // as seen on the robot 2026-10-09
    EXPECT_DOUBLE_EQ(*t.batteryV, 6.7);
    EXPECT_FALSE(t.any);  // not an encoder report
    EXPECT_FALSE(driver::yahboom::parseReport("Battery:xV", t));
    EXPECT_FALSE(driver::yahboom::parseReport("Battery:", t));
    EXPECT_DOUBLE_EQ(*t.batteryV, 6.7);  // a bad reply leaves the last good value
}

TEST(YahboomBattery, FramedReplyThroughParser) {
    driver::yahboom::FrameParser p;
    const auto frames = p.feed("$Battery:6.7V#");
    ASSERT_EQ(frames.size(), 1u);
    driver::yahboom::Telemetry t;
    EXPECT_TRUE(driver::yahboom::parseReport(frames[0], t));
}

TEST(Battery, StatesAndHysteresis) {
    BatteryMonitor m;  // warn 7.0, low 6.6, hysteresis 0.1
    EXPECT_EQ(m.update(std::nullopt, -1), BatteryState::Unknown);
    EXPECT_EQ(m.update(8.0, 0.5), BatteryState::Ok);
    EXPECT_EQ(m.update(6.9, 0.5), BatteryState::Warn);
    EXPECT_EQ(m.update(7.0, 0.5), BatteryState::Warn);  // needs 7.1 to recover
    EXPECT_EQ(m.update(7.1, 0.5), BatteryState::Ok);
    EXPECT_EQ(m.update(6.5, 0.5), BatteryState::Low);
    EXPECT_EQ(m.update(6.6, 0.5), BatteryState::Low);   // needs 6.7 to leave Low
    EXPECT_EQ(m.update(6.7, 0.5), BatteryState::Warn);
}

TEST(Battery, StaleReadingIsUnknownAndNeverBlocks) {
    BatteryMonitor m;
    m.update(6.0, 0.5);
    EXPECT_TRUE(m.armBlocked());
    EXPECT_EQ(m.update(6.0, 6.0), BatteryState::Unknown);
    EXPECT_FALSE(m.armBlocked());
}

TEST(Battery, ArmBlockThresholdAndDisable) {
    BatteryMonitor m;
    m.update(6.6, 0.5);
    EXPECT_FALSE(m.armBlocked());  // at the threshold is allowed
    m.update(6.5, 0.5);
    EXPECT_TRUE(m.armBlocked());
    exec::BatteryConfig off;
    off.blockArmBelowV = 0;
    BatteryMonitor n(off);
    n.update(5.0, 0.5);
    EXPECT_FALSE(n.armBlocked());
}

TEST(Battery, LipoPercent) {
    EXPECT_DOUBLE_EQ(exec::lipoPercent(4.25), 100);
    EXPECT_DOUBLE_EQ(exec::lipoPercent(3.0), 0);
    EXPECT_DOUBLE_EQ(exec::lipoPercent(3.84), 50);
    EXPECT_NEAR(exec::lipoPercent(3.35), 1.2, 0.1);  // 6.7 V on 2S: nearly empty
}

TEST(Battery, TeleopRefusesArmWhenBlocked) {
    exec::TeleopController c;
    exec::TeleopInputs i;
    i.arm = true;
    i.armBlocked = true;
    c.update(i, 0.02f);
    EXPECT_EQ(c.mode(), exec::TeleopMode::Disarmed);
    EXPECT_EQ(c.reason(), "arm refused: battery low");
    i.arm = false;
    c.update(i, 0.02f);
    i.arm = true;
    i.armBlocked = false;
    c.update(i, 0.02f);
    EXPECT_EQ(c.mode(), exec::TeleopMode::Armed);
}

namespace {
driver::BoardStatus board(std::optional<double> v, double age = 0.5) {
    driver::BoardStatus b;
    b.supported = true;
    b.batteryV = v;
    b.batteryAgeS = v ? age : -1;
    b.replyAgeS = age;
    b.replies = 10;
    return b;
}

void pressStart(exec::ManualDrive& md) {
    exec::TeleopInputs a;
    md.step({}, 0.02f);
    a.arm = true;
    md.step(a, 0.02f);
}
}  // namespace

TEST(Battery, ManualDriveBlocksArmOnLowBatteryAndReports) {
    driver::SimMotorController m;
    m.setBoard(board(6.4));
    exec::ManualDrive md(m, nullptr, nullptr, {});
    pressStart(md);
    auto s = md.status();
    EXPECT_EQ(s.mode, exec::TeleopMode::Disarmed);
    EXPECT_EQ(s.battery, BatteryState::Low);
    EXPECT_TRUE(s.batteryArmBlocked);
    ASSERT_TRUE(s.board.batteryV);
    EXPECT_DOUBLE_EQ(*s.board.batteryV, 6.4);

    m.setBoard(board(7.6));
    pressStart(md);
    EXPECT_EQ(md.status().mode, exec::TeleopMode::Armed);
    EXPECT_EQ(md.status().battery, BatteryState::Ok);
}

TEST(Battery, ManualDriveNoReadingNeverBlocks) {
    driver::SimMotorController m;  // dry-run: board status unsupported
    exec::ManualDrive md(m, nullptr, nullptr, {});
    pressStart(md);
    EXPECT_EQ(md.status().mode, exec::TeleopMode::Armed);
    EXPECT_EQ(md.status().battery, BatteryState::Unknown);
}
