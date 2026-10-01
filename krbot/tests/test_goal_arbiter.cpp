#include "exec/GoalArbiter.hpp"
#include "exec/StubExecutor.hpp"

#include <algorithm>
#include <gtest/gtest.h>

using namespace krbot;
using exec::BtStatus;

namespace {
reason::Goal goal(std::string type, int prio) { return {std::move(type), "t", prio, knowledge::FactSource::Inferred}; }

// Executor whose result the test controls.
struct ScriptedExec : exec::IBehaviorExecutor {
    BtStatus* result;
    bool* halted;
    BtStatus tick() override { return *result; }
    void halt() override { *halted = true; }
};
}  // namespace

TEST(GoalArbiter, PicksHighestPriority) {
    reason::GoalQueue q;
    std::vector<knowledge::Fact> facts;
    exec::GoalArbiter a(q, exec::stubExecutorFactory(), [&](knowledge::Fact f) { facts.push_back(f); }, 10);
    q.push(goal("Idle", 0));
    q.push(goal("HoldPosition", 100));
    a.tick();
    ASSERT_TRUE(a.activeGoal());
    EXPECT_EQ(a.activeGoal()->type, "HoldPosition");
    EXPECT_EQ(a.pendingCount(), 1u);
}

TEST(GoalArbiter, HysteresisPreventsThrashing) {
    reason::GoalQueue q;
    exec::GoalArbiter a(q, exec::stubExecutorFactory(), {}, 10);
    q.push(goal("HoldPosition", 60));
    a.tick();
    q.push(goal("Idle", 65));  // within margin: no pre-emption
    a.tick();
    EXPECT_EQ(a.activeGoal()->priority, 60);
    q.push(goal("HoldPosition", 100));  // beats 60 + 10
    a.tick();
    EXPECT_EQ(a.activeGoal()->priority, 100);
}

TEST(GoalArbiter, PreemptionHaltsAndOutcomeIsReported) {
    reason::GoalQueue q;
    BtStatus result = BtStatus::Running;
    bool halted = false;
    std::vector<knowledge::Fact> facts;
    exec::GoalArbiter a(
        q,
        [&](const reason::Goal&) {
            auto e = std::make_unique<ScriptedExec>();
            e->result = &result;
            e->halted = &halted;
            return e;
        },
        [&](knowledge::Fact f) { facts.push_back(f); }, 10);
    q.push(goal("Navigate", 50));
    a.tick();
    q.push(goal("HoldPosition", 100));
    a.tick();
    EXPECT_TRUE(halted);
    const auto it = std::find_if(facts.begin(), facts.end(), [](const auto& f) { return f.predicate == "outcome"; });
    ASSERT_NE(it, facts.end());
    EXPECT_EQ(it->objectAsSymbol(), "Blocked");
    EXPECT_EQ(it->source, knowledge::FactSource::Execution);
}

TEST(GoalArbiter, CompletionFallsBackToNextPending) {
    reason::GoalQueue q;
    std::vector<knowledge::Fact> facts;
    exec::GoalArbiter a(q, exec::stubExecutorFactory(), [&](knowledge::Fact f) { facts.push_back(f); }, 10);
    q.push(goal("Idle", 0));
    q.push(goal("ReturnToBase", 70));  // stub has no tree for it -> Failure on first tick
    a.tick();
    EXPECT_FALSE(a.activeGoal());
    a.tick();
    ASSERT_TRUE(a.activeGoal());
    EXPECT_EQ(a.activeGoal()->type, "Idle");
}
