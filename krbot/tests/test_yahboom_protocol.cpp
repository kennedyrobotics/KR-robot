// Ported from tests/test_bringup.py TestYahboomFraming — same cases, same expectations.
#include "driver/YahboomProtocol.hpp"

#include <gtest/gtest.h>

using namespace krbot::driver::yahboom;

TEST(YahboomProtocol, Format) {
    EXPECT_EQ(formatCommand("pwm", {100, -200, 0, 0}), "$pwm:100,-200,0,0#");
    EXPECT_EQ(formatCommand("mtype", {4}), "$mtype:4#");
    EXPECT_EQ(formatCommandFloat("wdiameter", 67.0), "$wdiameter:67.00#");
}

TEST(YahboomProtocol, ProfileCommands) {
    EXPECT_EQ(profileCommands(kProfile33GB520), (std::vector<std::string>{"$mtype:4#", "$deadzone:1000#"}));
    EXPECT_EQ(profileCommands(kProfileMD520Z30),
              (std::vector<std::string>{"$mtype:1#", "$mphase:30#", "$mline:11#", "$wdiameter:67.00#", "$deadzone:1900#"}));
}

TEST(YahboomProtocol, FramesSplitAndPartial) {
    FrameParser p;
    EXPECT_EQ(p.feed("junk$MAll:1, 2, 3, 4#$MTEP:5,6"), (std::vector<std::string>{"MAll:1, 2, 3, 4"}));
    EXPECT_EQ(p.feed(",7,8#"), (std::vector<std::string>{"MTEP:5,6,7,8"}));
    EXPECT_EQ(p.pending(), 0u);
}

TEST(YahboomProtocol, GarbageWithoutTerminatorIsBounded) {
    FrameParser p;
    p.feed("$" + std::string(5000, 'x'));
    EXPECT_EQ(p.pending(), 0u);
}

TEST(YahboomProtocol, ParseReport) {
    Telemetry t;
    EXPECT_TRUE(parseReport("MAll:10, -20, 30, 40", t));
    EXPECT_EQ(t.totalPulses, (std::array<int32_t, 4>{10, -20, 30, 40}));
    EXPECT_TRUE(parseReport("MSPD:1.5,0,0,-2.25", t));
    EXPECT_DOUBLE_EQ(t.speedMmS[0], 1.5);
    EXPECT_DOUBLE_EQ(t.speedMmS[3], -2.25);
    EXPECT_FALSE(parseReport("ok", t));
    EXPECT_FALSE(parseReport("MAll:x,y", t));
    EXPECT_TRUE(t.any);
}
