// krbot - KR-bot 5-layer C++ stack entry point.
//
//   krbot [--config=/path/krbot.conf] [--dry-run] [--motor.port=/dev/krc-motor]
//         [--teleop.max_output=1200] [--input.device=/dev/input/event5] [--log=debug]
//
// Any config key can be overridden as --section.key=value. SIGINT/SIGTERM -> motors zeroed, exit 0.
// SIGUSR1 -> latched E-STOP (re-arm only from the gamepad), process keeps running.

#include "common/Config.hpp"
#include "common/Log.hpp"
#include "core/App.hpp"

#include <csignal>
#include <cstdio>
#include <filesystem>
#include <string>
#include <thread>
#include <unistd.h>
#include <vector>

namespace {
volatile std::sig_atomic_t g_stop = 0;
volatile std::sig_atomic_t g_estop = 0;
void onSignal(int) { g_stop = 1; }
void onEstopSignal(int) { g_estop = 1; }  // SIGUSR1: external e-stop (KR-bot Console, systemctl kill)

std::string defaultConfigPath(const char* argv0) {
    namespace fs = std::filesystem;
    std::error_code ec;
    const auto exe = fs::weakly_canonical(fs::path(argv0), ec);
    for (const auto& c : {exe.parent_path() / "../config/krbot.conf", exe.parent_path() / "../../config/krbot.conf",
                          fs::path("/etc/krbot/krbot.conf")}) {
        if (fs::exists(c, ec)) return c.string();
    }
    return {};
}
}  // namespace

int main(int argc, char** argv) {
    std::vector<std::string> args(argv + 1, argv + argc);
    for (const auto& a : args) {
        if (a == "-h" || a == "--help") {
            std::puts("krbot [--config=FILE] [--dry-run] [--log=debug|info|warn|error] [--section.key=value ...]");
            return 0;
        }
    }

    krbot::common::Config probe;
    probe.applyArgs(args);
    std::string cfgPath = probe.getString("config", defaultConfigPath(argv[0]));
    krbot::common::Config cfg;
    if (!cfgPath.empty()) {
        if (auto c = krbot::common::Config::fromFile(cfgPath)) {
            cfg = *c;
        } else {
            std::fprintf(stderr, "cannot read config %s\n", cfgPath.c_str());
            return 2;
        }
    }
    for (const auto& unknown : cfg.applyArgs(args)) std::fprintf(stderr, "ignoring argument: %s\n", unknown.c_str());
    if (cfg.getBool("dry-run", false)) cfg.set("motor.driver", "sim");
    krbot::common::setLogLevel(krbot::common::parseLogLevel(cfg.getString("log", "info")));
    KLOG_INFO("main", "krbot 0.1.0 - config: {}", cfgPath.empty() ? "(defaults)" : cfgPath);

    std::signal(SIGINT, onSignal);
    std::signal(SIGTERM, onSignal);
    std::signal(SIGUSR1, onEstopSignal);

    krbot::core::App app(cfg);
    app.start();
    const auto statusEvery = std::chrono::milliseconds(cfg.getInt("status_period_ms", 2000));
    auto nextStatus = std::chrono::steady_clock::now();
    while (!g_stop) {
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
        if (g_estop) {
            g_estop = 0;
            app.requestEstop("external e-stop (SIGUSR1)");
            app.logStatus();
        }
        if (std::chrono::steady_clock::now() >= nextStatus) {
            app.logStatus();
            nextStatus += statusEvery;
        }
    }
    app.stop();
    return 0;
}
