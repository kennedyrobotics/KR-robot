#pragma once
// L3 fact tuple - doc v3 §4: (subject, predicate, object, confidence, timestamp, source, ttl).
// Shape deliberately mirrors the generic CLIPS `fact` deftemplate (doc v3 §5.2) so the L3->L4
// sync stays a mechanical translation.

#include <chrono>
#include <cstdint>
#include <string>
#include <string_view>
#include <variant>

namespace krbot::knowledge {

enum class FactSource { Perception, Operator, Inferred, Telemetry, Execution, ConfigSeed };

constexpr std::string_view toString(FactSource s) {
    switch (s) {
        case FactSource::Perception: return "Perception";
        case FactSource::Operator: return "Operator";
        case FactSource::Inferred: return "Inferred";
        case FactSource::Telemetry: return "Telemetry";
        case FactSource::Execution: return "Execution";
        case FactSource::ConfigSeed: return "ConfigSeed";
    }
    return "?";
}

using FactId = uint64_t;
using Clock = std::chrono::steady_clock;
using Object = std::variant<std::string, double>;  // CLIPS slot type SYMBOL | FLOAT

struct Fact {
    FactId id = 0;  // assigned by FactStore
    std::string subject;
    std::string predicate;
    Object object;
    double confidence = 1.0;
    Clock::time_point timestamp{};
    FactSource source = FactSource::Perception;
    std::chrono::milliseconds ttl{0};  // 0 = no expiry

    std::string objectAsSymbol() const;
    bool expired(Clock::time_point now) const { return ttl.count() > 0 && now - timestamp >= ttl; }
};

struct FactDelta {
    enum class Op { Assert, Retract, Modify };
    Op op;
    Fact fact;
};

}  // namespace krbot::knowledge
