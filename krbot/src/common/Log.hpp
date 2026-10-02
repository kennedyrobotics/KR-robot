#pragma once
// Minimal thread-safe leveled logger -> stderr (journald picks it up under systemd).

#include <format>
#include <functional>
#include <string>
#include <string_view>

namespace krbot::common {

enum class LogLevel { Debug = 0, Info, Warn, Error };

void setLogLevel(LogLevel level);
LogLevel logLevel();
LogLevel parseLogLevel(std::string_view s);
void logWrite(LogLevel level, std::string_view tag, std::string_view msg);
const char* toString(LogLevel level);

// Optional extra destination for every emitted line (e.g. the monitor server). Called with the
// logger's lock held, so the sink must be fast, non-blocking, and must never log itself.
using LogSink = std::function<void(LogLevel level, const std::string& line)>;
void setLogSink(LogSink sink);  // pass {} to remove

template <typename... Args>
void log(LogLevel level, std::string_view tag, std::format_string<Args...> fmt, Args&&... args) {
    if (level < logLevel()) return;
    logWrite(level, tag, std::format(fmt, std::forward<Args>(args)...));
}

}  // namespace krbot::common

#define KLOG_DEBUG(tag, ...) ::krbot::common::log(::krbot::common::LogLevel::Debug, tag, __VA_ARGS__)
#define KLOG_INFO(tag, ...) ::krbot::common::log(::krbot::common::LogLevel::Info, tag, __VA_ARGS__)
#define KLOG_WARN(tag, ...) ::krbot::common::log(::krbot::common::LogLevel::Warn, tag, __VA_ARGS__)
#define KLOG_ERROR(tag, ...) ::krbot::common::log(::krbot::common::LogLevel::Error, tag, __VA_ARGS__)
