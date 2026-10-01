#include "knowledge/FactStore.hpp"
#include "knowledge/FactTable.hpp"
#include "percep/ObservationMapper.hpp"
#include "reason/NullReasoner.hpp"

#include <gtest/gtest.h>
#include <thread>

using namespace krbot::knowledge;
using namespace std::chrono_literals;

namespace {
Fact mk(std::string s, std::string p, Object o, std::chrono::milliseconds ttl = 0ms) {
    Fact f;
    f.subject = std::move(s);
    f.predicate = std::move(p);
    f.object = std::move(o);
    f.ttl = ttl;
    return f;
}
}  // namespace

TEST(FactTable, AddQueryRetract) {
    FactTable t;
    const auto [a, da] = t.add(mk("obstacle1", "distanceTo", 0.4));
    t.add(mk("obstacle2", "distanceTo", 1.2));
    t.add(mk("robot", "teleopMode", std::string("ARMED")));
    EXPECT_EQ(da.op, FactDelta::Op::Assert);
    EXPECT_EQ(t.query({std::nullopt, "distanceTo"}).size(), 2u);
    EXPECT_EQ(t.query({"robot", std::nullopt}).size(), 1u);
    const auto d = t.retract(a);
    ASSERT_TRUE(d);
    EXPECT_EQ(d->op, FactDelta::Op::Retract);
    EXPECT_EQ(t.query({std::nullopt, "distanceTo"}).size(), 1u);
    EXPECT_FALSE(t.retract(a));
}

TEST(FactTable, UpsertModifiesFunctionalFact) {
    FactTable t;
    const auto [id1, d1] = t.upsert(mk("robot", "teleopMode", std::string("DISARMED")));
    const auto [id2, d2] = t.upsert(mk("robot", "teleopMode", std::string("ARMED")));
    EXPECT_EQ(id1, id2);
    EXPECT_EQ(d1.op, FactDelta::Op::Assert);
    EXPECT_EQ(d2.op, FactDelta::Op::Modify);
    EXPECT_EQ(t.size(), 1u);
    EXPECT_EQ(t.get(id1)->objectAsSymbol(), "ARMED");
}

TEST(FactTable, TtlExpiryEmitsRetract) {
    FactTable t;
    auto f = mk("obstacle1", "distanceTo", 0.3, 100ms);
    f.timestamp = Clock::now() - 200ms;
    t.add(f);
    t.add(mk("robot", "platform", std::string("tracked")));
    const auto deltas = t.expire(Clock::now());
    ASSERT_EQ(deltas.size(), 1u);
    EXPECT_EQ(deltas[0].op, FactDelta::Op::Retract);
    EXPECT_EQ(t.size(), 1u);
}

TEST(FactStore, ThreadedPathPublishesDeltasToReasoner) {
    FactStore store;
    store.start();
    store.upsert(mk("robot", "teleopMode", std::string("DISARMED")));
    store.upsert(mk("robot", "teleopMode", std::string("ARMED")));
    store.add(mk("gamepad", "linkHealthy", std::string("true")));
    const auto all = store.query({});
    EXPECT_EQ(all.size(), 2u);

    krbot::reason::NullReasoner r({});
    for (const auto& d : store.deltas().drain()) r.syncDelta(d);
    EXPECT_EQ(r.deltasSeen(), 3u);           // assert, modify, assert
    EXPECT_EQ(r.workingMemorySize(), 2u);    // mirrors FactStore (doc v3 §5.3 liveFacts_)
    store.stop();
}

TEST(ObservationMapper, DistanceIsMultiValuedWithTtl) {
    krbot::percep::Observation o{krbot::percep::Observation::Kind::Distance, "ultrasonicFront", 0.25};
    const auto m = krbot::percep::mapObservation(o);
    EXPECT_FALSE(m.functional);
    EXPECT_EQ(m.fact.predicate, "distanceTo");
    EXPECT_GT(m.fact.ttl.count(), 0);
    EXPECT_EQ(m.fact.source, FactSource::Perception);
}
