#include "common/Log.hpp"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <ctime>
#include <mutex>

namespace krbot::common {

namespace {
std::atomic<LogLevel> g_level{LogLevel::Info};
std::mutex g_mutex;
LogSink g_sink;  // guarded by g_mutex
constexpr const char* kNames[] = {"DEBUG", "INFO ", "WARN ", "ERROR"};
constexpr const char* kPlain[] = {"DEBUG", "INFO", "WARN", "ERROR"};
}  // namespace

const char* toString(LogLevel level) { return kPlain[static_cast<int>(level)]; }

void setLogSink(LogSink sink) {
    std::lock_guard lock(g_mutex);
    g_sink = std::move(sink);
}

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
    // Local time (std::format on a sys_time prints UTC, which disagreed with journald/console).
    const auto now = system_clock::now();
    const std::time_t t = system_clock::to_time_t(now);
    std::tm tm{};
    ::localtime_r(&t, &tm);
    const auto ms = duration_cast<milliseconds>(now.time_since_epoch()).count() % 1000;
    auto line = std::format("{:02}:{:02}:{:02}.{:03} {} [{}] {}", tm.tm_hour, tm.tm_min, tm.tm_sec, ms,
                            kNames[static_cast<int>(level)], tag, msg);
    std::lock_guard lock(g_mutex);
    std::fputs(line.c_str(), stderr);
    std::fputc('\n', stderr);
    if (g_sink) g_sink(level, line);
}

}  // namespace krbot::common
