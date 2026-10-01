#include "percep/ObservationMapper.hpp"

namespace krbot::percep {

using namespace std::chrono_literals;

MappedFact mapObservation(const Observation& o) {
    knowledge::Fact f;
    f.subject = o.sensor;
    f.object = o.value;
    f.confidence = o.confidence;
    f.timestamp = o.timestamp;
    f.source = knowledge::FactSource::Perception;
    switch (o.kind) {
        case Observation::Kind::Distance:
            f.predicate = "distanceTo";  // matches doc v3 §5.4 example rules
            f.ttl = 500ms;
            return {f, false};
        case Observation::Kind::LinkHealth:
            f.predicate = "linkHealthy";
            f.ttl = 2s;
            return {f, true};
        case Observation::Kind::EncoderSpeed:
            f.predicate = "speedMmS";
            f.source = knowledge::FactSource::Telemetry;
            f.ttl = 500ms;
            return {f, true};
        case Observation::Kind::Battery:
            f.predicate = "batteryVolts";
            f.source = knowledge::FactSource::Telemetry;
            f.ttl = 5s;
            return {f, true};
    }
    return {f, true};
}

}  // namespace krbot::percep
