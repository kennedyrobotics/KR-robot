#include "percep/MotorHealthSource.hpp"

namespace krbot::percep {

std::vector<Observation> MotorHealthSource::poll() {
    return {Observation{Observation::Kind::LinkHealth, "motorBoard",
                        std::string(motors_.isHealthy() ? "true" : "false")}};
}

}  // namespace krbot::percep
