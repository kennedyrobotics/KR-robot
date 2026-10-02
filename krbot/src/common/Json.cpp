#include "common/Json.hpp"

#include <format>

namespace krbot::common::json {

std::string quote(std::string_view s) {
    std::string out;
    out.reserve(s.size() + 2);
    out += '"';
    for (const char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20)
                    out += std::format("\\u{:04x}", static_cast<unsigned>(static_cast<unsigned char>(c)));
                else
                    out += c;  // UTF-8 passes through unchanged
        }
    }
    out += '"';
    return out;
}

std::optional<std::string> stringField(std::string_view obj, std::string_view key) {
    const std::string needle = quote(key);
    std::size_t pos = 0;
    while ((pos = obj.find(needle, pos)) != std::string_view::npos) {
        std::size_t i = pos + needle.size();
        while (i < obj.size() && (obj[i] == ' ' || obj[i] == '\t')) ++i;
        if (i >= obj.size() || obj[i] != ':') {
            pos += needle.size();  // it was a value, not a key
            continue;
        }
        ++i;
        while (i < obj.size() && (obj[i] == ' ' || obj[i] == '\t')) ++i;
        if (i >= obj.size() || obj[i] != '"') return std::nullopt;
        std::string val;
        for (++i; i < obj.size(); ++i) {
            if (obj[i] == '\\' && i + 1 < obj.size()) {
                const char e = obj[++i];
                val += e == 'n' ? '\n' : e == 't' ? '\t' : e;
            } else if (obj[i] == '"') {
                return val;
            } else {
                val += obj[i];
            }
        }
        return std::nullopt;  // unterminated
    }
    return std::nullopt;
}

}  // namespace krbot::common::json
