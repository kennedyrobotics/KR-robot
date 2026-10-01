#include "common/Config.hpp"

#include <gtest/gtest.h>

using krbot::common::Config;

TEST(Config, SectionsCommentsAndTypes) {
    const auto c = Config::fromString(
        "log = debug\n"
        "[motor]\n"
        "port = /dev/krc-motor   # udev symlink\n"
        "max_output = 1200\n"
        "[teleop]\n"
        "expo = 0.25\n"
        "tank = yes\n"
        "device =   # empty\n");
    EXPECT_EQ(c.getString("log", ""), "debug");
    EXPECT_EQ(c.getString("motor.port", ""), "/dev/krc-motor");
    EXPECT_EQ(c.getInt("motor.max_output", 0), 1200);
    EXPECT_DOUBLE_EQ(c.getDouble("teleop.expo", 0), 0.25);
    EXPECT_TRUE(c.getBool("teleop.tank", false));
    EXPECT_EQ(c.getString("teleop.device", "x"), "");
    EXPECT_EQ(c.getInt("missing", 7), 7);
}

TEST(Config, BadNumbersFallBackToDefault) {
    const auto c = Config::fromString("a = 12abc\nb = 1.5x\n");
    EXPECT_EQ(c.getInt("a", -1), -1);
    EXPECT_DOUBLE_EQ(c.getDouble("b", -1), -1);
}

TEST(Config, CommandLineOverrides) {
    auto c = Config::fromString("[motor]\nmax_output = 1800\n");
    const auto rest = c.applyArgs({"--motor.max_output=900", "--dry-run", "stray"});
    EXPECT_EQ(c.getInt("motor.max_output", 0), 900);
    EXPECT_TRUE(c.getBool("dry-run", false));
    EXPECT_EQ(rest, (std::vector<std::string>{"stray"}));
}
