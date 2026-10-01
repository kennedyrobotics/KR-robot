#pragma once
// L2 typed observation — doc v3 §3. Produced by sensor sources, filtered, then mapped to ontology
// terms by ObservationMapper (the only place raw sensor output becomes a fact).

#include "knowledge/Fact.hpp"

#include <string>
#include <vector>

namespace krbot::percep {

struct Observation {
    enum class Kind { Distance, LinkHealth, EncoderSpeed, Battery };
    Kind kind;
    std::string sensor;        // e.g. "motorBoard", "ultrasonicFront"
    knowledge::Object value;   // metres, true/false symbol, mm/s, volts...
    double confidence = 1.0;
    knowledge::Clock::time_point timestamp = knowledge::Clock::now();
};

// A pollable L2 source wrapping one or more L1 drivers.
class ISensorSource {
public:
    virtual std::vector<Observation> poll() = 0;
    virtual ~ISensorSource() = default;
};

}  // namespace krbot::percep
