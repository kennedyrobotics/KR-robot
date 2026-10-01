#pragma once
// FactStore — doc v3 §4: the single source of truth, owned exclusively by one dedicated thread.
// L2/L4/L5 never lock it; they submit commands through a queue. Every mutation is published as a
// FactDelta on deltas() (the L3->L4 queue, doc v3 §5.3). SQLite episodic persistence: Phase 4.

#include "common/ThreadSafeQueue.hpp"
#include "knowledge/FactTable.hpp"

#include <atomic>
#include <functional>
#include <future>
#include <thread>
#include <variant>

namespace krbot::knowledge {

class FactStore {
public:
    FactStore();
    ~FactStore();
    FactStore(const FactStore&) = delete;
    FactStore& operator=(const FactStore&) = delete;

    void start();
    void stop();

    // Fire-and-forget writes (the normal path for L2 observations and L5 outcomes).
    void upsert(Fact f);
    void add(Fact f);
    void retract(FactId id);

    // Synchronous read (blocks until the store thread answers).
    std::vector<Fact> query(FactQuery q);
    std::size_t size() const { return size_; }

    common::ThreadSafeQueue<FactDelta>& deltas() { return deltas_; }

private:
    struct Upsert { Fact f; };
    struct Add { Fact f; };
    struct Retract { FactId id; };
    struct Query { FactQuery q; std::shared_ptr<std::promise<std::vector<Fact>>> reply; };
    using Command = std::variant<Upsert, Add, Retract, Query>;

    void run();
    void publish(FactDelta d);

    FactTable table_;
    common::ThreadSafeQueue<Command> commands_;
    common::ThreadSafeQueue<FactDelta> deltas_{10000};
    std::atomic<bool> running_{false};
    std::atomic<std::size_t> size_{0};
    std::thread thread_;
};

}  // namespace krbot::knowledge
