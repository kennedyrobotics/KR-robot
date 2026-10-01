// Ported from tests/test_bringup.py TestAxis.
#include "exec/InputDriver.hpp"

#include <gtest/gtest.h>
#include <linux/input-event-codes.h>

using krbot::exec::AxisInfo;

TEST(Axis, XpadStickAndTrigger) {
    const AxisInfo stick{ABS_X, -32768, 32767, 128};
    EXPECT_NEAR(stick.normalise(32767), 1.0f, 1e-4);
    EXPECT_NEAR(stick.normalise(-32768), -1.0f, 1e-4);
    const AxisInfo trig{ABS_Z, 0, 255, 0};
    EXPECT_TRUE(trig.oneSided());
    EXPECT_FLOAT_EQ(trig.normalise(255), 1.0f);
}

TEST(Axis, Ps4StickIsCentred) {
    const AxisInfo stick{ABS_Y, 0, 255, 0};
    EXPECT_FALSE(stick.oneSided());
    EXPECT_FLOAT_EQ(stick.normalise(0), -1.0f);
}

TEST(Axis, XboxBtRightStickOnZIsCentred) { EXPECT_FALSE((AxisInfo{ABS_Z, 0, 65535, 0}).oneSided()); }

TEST(Axis, DegenerateRange) { EXPECT_EQ((AxisInfo{ABS_X, 5, 5, 0}).normalise(5), 0.0f); }
