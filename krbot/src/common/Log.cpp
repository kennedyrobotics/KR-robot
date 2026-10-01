#include "common/Log.hpp"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <mutex>

namespace krbot::common {

namespace {
std::atomic<LogLevel> g_level{LogLevel::Info};
std::mutex g_mutex;
constexpr const char* kNames[] = {"DEBUG", "INFO ", "WARN ", "ERROR"};
}  // namespace

void setLogLevel(LogLevel level) { g_level = level; }
LogLevel logLevel() { return g_level; }

LogLevel parseLogLevel(std::string_view s) {
    if (s == "debug") return LogLevel::Debug;
    if (s == "warn") return LogLevel::Warn;
    if (s == "error") return LogLevel::Error;
    return LogLevel::Info;
}

void logWrite(LogLevel level, std::string_view tag, std::string_view msg) {
    using namespace std::chrono;
    const auto now = floor<milliseconds>(system_clock::now());
    const auto line = std::format("{:%H:%M:%S} {} [{}] {}\n", now, kNames[static_cast<int>(level)], tag, msg);
    std::lock_guard lock(g_mutex);
    std::fputs(line.c_str(), stderr);
}

}  // namespace krbot::common
