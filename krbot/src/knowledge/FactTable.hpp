#pragma once
// Single-threaded fact table - the logic inside FactStore, separated so it is unit-testable.
// Indexed by subject and predicate (doc v3 §4: unordered_multimap on subject and predicate).
// Every mutation returns the FactDelta(s) to publish to L4.

#include "knowledge/Fact.hpp"

#include <optional>
#include <unordered_map>
#include <vector>

namespace krbot::knowledge {

struct FactQuery {
    std::optional<std::string> subject;
    std::optional<std::string> predicate;
};

class FactTable {
public:
    // Functional facts (one value per subject+predicate, e.g. robot/teleopMode): an existing
    // fact with the same key is modified in place (Modify delta). Otherwise a new fact is added.
    std::pair<FactId, FactDelta> upsert(Fact f);
    // Multi-valued facts (e.g. several obstacles): always adds.
    std::pair<FactId, FactDelta> add(Fact f);
    std::optional<FactDelta> retract(FactId id);
    std::vector<FactDelta> expire(Clock::time_point now);

    std::vector<Fact> query(const FactQuery& q) const;
    std::optional<Fact> get(FactId id) const;
    std::size_t size() const { return facts_.size(); }

private:
    void index(const Fact& f);
    void unindex(const Fact& f);

    FactId nextId_ = 1;
    std::unordered_map<FactId, Fact> facts_;
    std::unordered_multimap<std::string, FactId> bySubject_;
    std::unordered_multimap<std::string, FactId> byPredicate_;
};

}  // namespace krbot::knowledge
