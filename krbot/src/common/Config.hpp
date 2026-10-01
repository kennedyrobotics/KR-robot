#pragma once
// Flat "section.key = value" config (INI-style [section] headers allowed), with command-line
// overrides of the form --section.key=value. Deliberately dependency-free.

#include <map>
#include <optional>
#include <string>
#include <vector>

namespace krbot::common {

class Config {
public:
    static Config fromString(const std::string& text);
    static std::optional<Config> fromFile(const std::string& path);

    // Applies --key=value / --flag arguments; returns the arguments it did not understand.
    std::vector<std::string> applyArgs(const std::vector<std::string>& args);

    void set(const std::string& key, const std::string& value) { values_[key] = value; }
    bool has(const std::string& key) const { return values_.count(key) != 0; }

    std::string getString(const std::string& key, const std::string& def) const;
    long getInt(const std::string& key, long def) const;
    double getDouble(const std::string& key, double def) const;
    bool getBool(const std::string& key, bool def) const;

    const std::map<std::string, std::string>& values() const { return values_; }

private:
    std::map<std::string, std::string> values_;
};

}  // namespace krbot::common
