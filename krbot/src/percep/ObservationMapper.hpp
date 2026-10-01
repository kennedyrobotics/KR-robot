#pragma once
// Observation -> Fact (ontology mapping at the L2->L3 boundary, doc v3 §3).

#include "knowledge/Fact.hpp"
#include "percep/Observation.hpp"

namespace krbot::percep {

struct MappedFact {
    knowledge::Fact fact;
    bool functional;  // true -> FactStore::upsert (one value per subject+predicate)
};

MappedFact mapObservation(const Observation& o);

}  // namespace krbot::percep
