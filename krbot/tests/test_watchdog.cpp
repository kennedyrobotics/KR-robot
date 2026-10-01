#include "driver/SimMotorController.hpp"
#include "exec/Watchdog.hpp"

#include <gtest/gtest.h>
#include <thread>

using namespace krbot;
using namespace std::chrono_literals;
using Clock = exec::Watchdog::Clock;

TEST(Watchdog, TripsOnceWhenNotKickedAndStopsMotorsDirectly) {
    driver::SimMotorController m;
    m.setAllChannels({500, -500, 0, 0});
    int handlerCalls = 0;
    exec::Watchdog wd(m, 100ms, [&](const char*) { ++handlerCalls; });
    wd.kick();
    EXPECT_FALSE(wd.check(Clock::now() + 50ms));
    EXPECT_TRUE(wd.check(Clock::now() + 150ms));
    EXPECT_EQ(m.last(), (driver::IMotorController::Channels{}));
    EXPECT_EQ(m.stops(), 1u);
    EXPECT_FALSE(wd.check(Clock::now() + 300ms));  // latched: one trip per stall
    EXPECT_EQ(handlerCalls, 1);
    wd.kick();  // loop recovered
    EXPECT_FALSE(wd.tripped());
}

TEST(Watchdog, ThreadDetectsRealStall) {
    driver::SimMotorController m;
    exec::Watchdog wd(m, 60ms);
    wd.start();
    for (int i = 0; i < 10; ++i) {  // healthy: kicked every 10 ms
        wd.kick();
        std::this_thread::sleep_for(10ms);
    }
    EXPECT_EQ(wd.trips(), 0u);
    std::this_thread::sleep_for(150ms);  // stall
    EXPECT_EQ(wd.trips(), 1u);
    wd.stop();
}
