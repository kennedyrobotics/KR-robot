#include "common/Config.hpp"

#include <algorithm>
#include <cctype>
#include <charconv>
#include <fstream>
#include <sstream>

namespace krbot::common {

namespace {
std::string trim(const std::string& s) {
    const auto b = s.find_first_not_of(" \t\r\n");
    if (b == std::string::npos) return {};
    const auto e = s.find_last_not_of(" \t\r\n");
    return s.substr(b, e - b + 1);
}
}  // namespace

Config Config::fromString(const std::string& text) {
    Config c;
    std::istringstream in(text);
    std::string line, section;
    while (std::getline(in, line)) {
        line = trim(line.substr(0, line.find_first_of("#;")));
        if (line.empty()) continue;
        if (line.front() == '[' && line.back() == ']') {
            section = trim(line.substr(1, line.size() - 2));
            continue;
        }
        const auto eq = line.find('=');
        if (eq == std::string::npos) continue;
        auto key = trim(line.substr(0, eq));
        if (!section.empty()) key = section + "." + key;
        c.values_[key] = trim(line.substr(eq + 1));
    }
    return c;
}

std::optional<Config> Config::fromFile(const std::string& path) {
    std::ifstream f(path);
    if (!f) return std::nullopt;
    std::stringstream ss;
    ss << f.rdbuf();
    return fromString(ss.str());
}

std::vector<std::string> Config::applyArgs(const std::vector<std::string>& args) {
    std::vector<std::string> rest;
    for (const auto& a : args) {
        if (a.rfind("--", 0) != 0) {
            rest.push_back(a);
            continue;
        }
        const auto eq = a.find('=');
        if (eq == std::string::npos)
            values_[a.substr(2)] = "true";  // bare flag
        else
            values_[a.substr(2, eq - 2)] = a.substr(eq + 1);
    }
    return rest;
}

std::string Config::getString(const std::string& key, const std::string& def) const {
    const auto it = values_.find(key);
    return it == values_.end() ? def : it->second;
}

long Config::getInt(const std::string& key, long def) const {
    const auto it = values_.find(key);
    if (it == values_.end()) return def;
    long v{};
    const auto& s = it->second;
    const auto [p, ec] = std::from_chars(s.data(), s.data() + s.size(), v);
    return (ec == std::errc{} && p == s.data() + s.size()) ? v : def;
}

double Config::getDouble(const std::string& key, double def) const {
    const auto it = values_.find(key);
    if (it == values_.end()) return def;
    try {
        std::size_t used = 0;
        const double v = std::stod(it->second, &used);
        return used == it->second.size() ? v : def;
    } catch (...) {
        return def;
    }
}

bool Config::getBool(const std::string& key, bool def) const {
    const auto it = values_.find(key);
    if (it == values_.end()) return def;
    std::string v = it->second;
    std::transform(v.begin(), v.end(), v.begin(), [](unsigned char ch) { return std::tolower(ch); });
    if (v == "1" || v == "true" || v == "yes" || v == "on") return true;
    if (v == "0" || v == "false" || v == "no" || v == "off") return false;
    return def;
}

}  // namespace krbot::common
