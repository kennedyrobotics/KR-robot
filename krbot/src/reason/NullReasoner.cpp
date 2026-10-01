#include "reason/NullReasoner.hpp"

namespace krbot::reason {

void NullReasoner::syncDelta(const knowledge::FactDelta& d) {
    ++deltas_;
    switch (d.op) {
        case knowledge::FactDelta::Op::Assert:
        case knowledge::FactDelta::Op::Modify:  // doc v3 §5.3: modify = retract + reassert
            live_[d.fact.id] = d.fact;
            break;
        case knowledge::FactDelta::Op::Retract:
            live_.erase(d.fact.id);
            break;
    }
}

long long NullReasoner::tick(int /*runLimit*/) { return 0; }

}  // namespace krbot::reason
