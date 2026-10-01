#include "knowledge/FactTable.hpp"

#include <format>

namespace krbot::knowledge {

std::string Fact::objectAsSymbol() const {
    if (const auto* s = std::get_if<std::string>(&object)) return *s;
    return std::format("{}", std::get<double>(object));
}

void FactTable::index(const Fact& f) {
    bySubject_.emplace(f.subject, f.id);
    byPredicate_.emplace(f.predicate, f.id);
}

void FactTable::unindex(const Fact& f) {
    for (auto* idx : {&bySubject_, &byPredicate_}) {
        const auto& key = idx == &bySubject_ ? f.subject : f.predicate;
        auto [b, e] = idx->equal_range(key);
        for (auto it = b; it != e; ++it) {
            if (it->second == f.id) {
                idx->erase(it);
                break;
            }
        }
    }
}

std::pair<FactId, FactDelta> FactTable::add(Fact f) {
    f.id = nextId_++;
    if (f.timestamp == Clock::time_point{}) f.timestamp = Clock::now();
    index(f);
    auto [it, _] = facts_.emplace(f.id, std::move(f));
    return {it->first, FactDelta{FactDelta::Op::Assert, it->second}};
}

std::pair<FactId, FactDelta> FactTable::upsert(Fact f) {
    for (const auto& existing : query({f.subject, f.predicate})) {
        auto& cur = facts_.at(existing.id);
        f.id = cur.id;
        if (f.timestamp == Clock::time_point{}) f.timestamp = Clock::now();
        cur = std::move(f);
        return {cur.id, FactDelta{FactDelta::Op::Modify, cur}};
    }
    return add(std::move(f));
}

std::optional<FactDelta> FactTable::retract(FactId id) {
    const auto it = facts_.find(id);
    if (it == facts_.end()) return std::nullopt;
    FactDelta d{FactDelta::Op::Retract, it->second};
    unindex(it->second);
    facts_.erase(it);
    return d;
}

std::vector<FactDelta> FactTable::expire(Clock::time_point now) {
    std::vector<FactId> dead;
    for (const auto& [id, f] : facts_)
        if (f.expired(now)) dead.push_back(id);
    std::vector<FactDelta> out;
    for (const auto id : dead)
        if (auto d = retract(id)) out.push_back(std::move(*d));
    return out;
}

std::vector<Fact> FactTable::query(const FactQuery& q) const {
    std::vector<Fact> out;
    auto matches = [&](const Fact& f) {
        return (!q.subject || f.subject == *q.subject) && (!q.predicate || f.predicate == *q.predicate);
    };
    if (q.subject) {
        auto [b, e] = bySubject_.equal_range(*q.subject);
        for (auto it = b; it != e; ++it)
            if (const auto& f = facts_.at(it->second); matches(f)) out.push_back(f);
    } else if (q.predicate) {
        auto [b, e] = byPredicate_.equal_range(*q.predicate);
        for (auto it = b; it != e; ++it) out.push_back(facts_.at(it->second));
    } else {
        for (const auto& [_, f] : facts_) out.push_back(f);
    }
    return out;
}

std::optional<Fact> FactTable::get(FactId id) const {
    const auto it = facts_.find(id);
    if (it == facts_.end()) return std::nullopt;
    return it->second;
}

}  // namespace krbot::knowledge
