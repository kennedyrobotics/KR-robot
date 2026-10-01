#include "common/ThreadSafeQueue.hpp"

#include <gtest/gtest.h>
#include <thread>

using krbot::common::ThreadSafeQueue;
using namespace std::chrono_literals;

TEST(ThreadSafeQueue, FifoAndDrain) {
    ThreadSafeQueue<int> q;
    q.push(1);
    q.push(2);
    q.push(3);
    EXPECT_EQ(q.tryPop(), 1);
    EXPECT_EQ(q.drain(), (std::vector<int>{2, 3}));
    EXPECT_FALSE(q.tryPop().has_value());
}

TEST(ThreadSafeQueue, BoundedRejectsWhenFull) {
    ThreadSafeQueue<int> q(2);
    EXPECT_TRUE(q.push(1));
    EXPECT_TRUE(q.push(2));
    EXPECT_FALSE(q.push(3));
}

TEST(ThreadSafeQueue, CloseWakesWaiter) {
    ThreadSafeQueue<int> q;
    std::thread t([&] {
        std::this_thread::sleep_for(20ms);
        q.close();
    });
    const auto t0 = std::chrono::steady_clock::now();
    EXPECT_FALSE(q.popFor(5s).has_value());
    EXPECT_LT(std::chrono::steady_clock::now() - t0, 1s);
    EXPECT_FALSE(q.push(1));
    t.join();
}
