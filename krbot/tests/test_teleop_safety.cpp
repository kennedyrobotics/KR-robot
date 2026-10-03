// Ported from tests/test_bringup.py TestMixing / TestSafety — the C++ L5 must behave identically.
#include "exec/TeleopSafety.hpp"

#include <gtest/gtest.h>

using namespace krbot::exec;

namespace {
constexpr float kDt = 0.02f;

TeleopOutput run(TeleopController& c, const TeleopInputs& in, int n = 200) {
    TeleopOutput out;
    for (int i = 0; i < n; ++i) out = c.update(in, kDt);
    return out;
}

TeleopInputs in(float ly = 0, bool deadman = false, bool arm = false, bool estop = false, bool link = true) {
    TeleopInputs i;
    i.ly = ly;
    i.deadman = deadman;
    i.arm = arm;
    i.estop = estop;
    i.linkOk = link;
    return i;
}

class Safety : public ::testing::Test {
protected:
    TeleopConfig cfg = [] {
        TeleopConfig c;
        c.maxOutput = 1000;
        c.invertRight = false;
        c.expo = 0;
        return c;
    }();
    TeleopController ctl{cfg};

    void arm() {
        run(ctl, in(), 3);  // release everything first (deadman release is debounced 40 ms)
        ctl.update(in(0, false, true), kDt);
        ctl.update(in(), kDt);
        ASSERT_EQ(ctl.mode(), TeleopMode::Armed);
    }
};
}  // namespace

TEST(Mixing, Deadband) {
    EXPECT_EQ(deadband(0.05f, 0.08f), 0.0f);
    EXPECT_FLOAT_EQ(deadband(1.0f, 0.08f), 1.0f);
    EXPECT_FLOAT_EQ(deadband(-1.0f, 0.08f), -1.0f);
}

TEST(Mixing, Arcade) {
    EXPECT_EQ(arcadeMix(1, 0), (std::pair<float, float>{1, 1}));
    EXPECT_EQ(arcadeMix(0, 1), (std::pair<float, float>{1, -1}));
    EXPECT_EQ(arcadeMix(1, 1), (std::pair<float, float>{1, 0}));
}

TEST_F(Safety, StartsDisarmedAndIgnoresSticks) {
    const auto o = run(ctl, in(-1, true));
    EXPECT_EQ(o.left, 0);
    EXPECT_EQ(o.right, 0);
    EXPECT_EQ(ctl.mode(), TeleopMode::Disarmed);
}

TEST_F(Safety, ArmRefusedWithDeadmanOrStick) {
    ctl.update(in(0, true, true), kDt);
    EXPECT_EQ(ctl.mode(), TeleopMode::Disarmed);
    ctl.update(in(), kDt);
    ctl.update(in(-0.5f, false, true), kDt);
    EXPECT_EQ(ctl.mode(), TeleopMode::Disarmed);
}

TEST_F(Safety, DriveNeedsDeadman) {
    arm();
    EXPECT_EQ(run(ctl, in(-1)).left, 0);
    const auto o = run(ctl, in(-1, true));
    EXPECT_EQ(o.left, 1000);
    EXPECT_EQ(o.right, 1000);
    // release deadman: stops once it has read released for deadmanReleaseMs (40 ms = 2 ticks)
    EXPECT_EQ(ctl.update(in(-1), kDt).left, 1000);
    const auto r = ctl.update(in(-1), kDt);
    EXPECT_EQ(r.left, 0);
    EXPECT_EQ(r.right, 0);
}

TEST_F(Safety, DeadmanDropoutDoesNotStop) {
    // SN2403 over BT: single all-buttons-up reports while LB is held (notes/bluetooth-debugging.md §8)
    arm();
    run(ctl, in(-1, true));
    for (int k = 0; k < 20; ++k) {
        EXPECT_EQ(ctl.update(in(-1, false), kDt).left, 1000) << "one-tick drop-out " << k;
        EXPECT_EQ(ctl.update(in(-1, true), kDt).left, 1000);
    }
}

TEST_F(Safety, DeadmanReleaseZeroIsImmediate) {
    cfg.deadmanReleaseMs = 0;
    TeleopController c{cfg};
    c.update(in(0, false, true), kDt);
    c.update(in(), kDt);
    run(c, in(-1, true));
    EXPECT_EQ(c.update(in(-1), kDt).left, 0);
}

TEST_F(Safety, LinkLossStopsWithoutDebounce) {
    arm();
    run(ctl, in(-1, true));
    EXPECT_EQ(ctl.update(in(-1, true, false, false, false), kDt).left, 0);
    EXPECT_EQ(ctl.mode(), TeleopMode::Disarmed);
}

TEST_F(Safety, ArmRefusedDuringDeadmanDropout) {
    // LB held (latched) -> one drop-out tick coinciding with START must not let it arm
    ctl.update(in(0, true), kDt);
    ctl.update(in(0, false, true), kDt);
    EXPECT_EQ(ctl.mode(), TeleopMode::Disarmed);
}

TEST_F(Safety, SlewLimitsRampUp) {
    arm();
    const auto o = ctl.update(in(-1, true), kDt);
    EXPECT_LE(o.left, static_cast<int>(cfg.slewPerS * kDt * 1000) + 1);
}

TEST_F(Safety, EstopLatchesUntilRearm) {
    arm();
    run(ctl, in(-1, true));
    EXPECT_EQ(ctl.update(in(-1, true, false, true), kDt).left, 0);
    EXPECT_EQ(run(ctl, in(-1, true)).left, 0);
    EXPECT_EQ(ctl.mode(), TeleopMode::Estop);
    arm();
}

TEST_F(Safety, LinkLossDisarms) {
    arm();
    run(ctl, in(-1, true));
    EXPECT_EQ(ctl.update(in(0, false, false, false, false), kDt).left, 0);
    EXPECT_EQ(ctl.mode(), TeleopMode::Disarmed);
    EXPECT_EQ(run(ctl, in(-1, true)).left, 0);  // link back, sticks held: no resume without re-arm
}

TEST_F(Safety, EstopSurvivesLinkLoss) {
    ctl.forceEstop("gui");
    run(ctl, in(0, false, false, false, false), 5);
    EXPECT_EQ(ctl.mode(), TeleopMode::Estop);
}

TEST(SafetyChannels, InversionAndChannelMap) {
    TeleopConfig c;
    c.maxOutput = 1000;
    c.invertRight = true;
    c.expo = 0;
    TeleopController ctl(c);
    ctl.update(in(0, false, true), kDt);
    ctl.update(in(), kDt);
    const auto o = run(ctl, in(-1, true));
    EXPECT_EQ(o.left, 1000);
    EXPECT_EQ(o.right, -1000);
    EXPECT_EQ(ctl.channels(o), (std::array<int16_t, 4>{1000, -1000, 0, 0}));
}
