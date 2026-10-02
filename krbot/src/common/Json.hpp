#pragma once
// Minimal JSON helpers for the monitor protocol (line-delimited JSON). Writing only needs string
// escaping; reading only needs to pull a top-level string field out of a small command object
// like {"cmd":"estop"}. Deliberately not a general parser.

#include <optional>
#include <string>
#include <string_view>

namespace krbot::common::json {

// Returns s as a quoted, escaped JSON string literal.
std::string quote(std::string_view s);

inline const char* boolean(bool b) { return b ? "true" : "false"; }

// Value of a top-level "key":"value" string member, or nullopt. Handles \" and \\ escapes.
std::optional<std::string> stringField(std::string_view obj, std::string_view key);

}  // namespace krbot::common::json
