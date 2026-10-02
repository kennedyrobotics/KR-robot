#pragma once
// MonitorServer - the server half of KR-bot monitoring (L5 "Operator Interface", observability).
//
// Protocol v1: line-delimited JSON over TCP (one object per '\n'-terminated line).
//   server -> client
//     {"type":"hello","server":"krbot","version":"...","protocol":1,"commands":["estop","ping"]}
//     {"type":"log","level":"WARN","line":"..."}      on connect: replay of recent lines, then live
//     {"type":"status", ...snapshot...}               at rate_hz
//     {"type":"ack","cmd":"estop"} / {"type":"pong"} / {"type":"error","msg":"..."}
//   client -> server
//     {"cmd":"estop"}   latched E-STOP (same as SIGUSR1). There is deliberately NO arm/drive
//     {"cmd":"ping"}    command: arming stays a gamepad action (docs/system-design.md §4).
//
// Binds 127.0.0.1 by default; set monitor.bind=0.0.0.0 (and open the port in ufw) for LAN clients.
// One thread, poll()-driven, non-blocking sockets; a client that stops reading is dropped rather
// than ever slowing the robot down.

#include "common/ThreadSafeQueue.hpp"

#include <atomic>
#include <cstdint>
#include <deque>
#include <functional>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace krbot::core {

class MonitorServer {
public:
    struct Options {
        std::string bind = "127.0.0.1";
        uint16_t port = 5765;  // 0 = ephemeral (tests)
        int rateHz = 10;
        std::size_t maxClients = 8;
        std::size_t replayLines = 100;
    };
    using SnapshotFn = std::function<std::string()>;  // returns the status members, without braces/type
    using CommandFn = std::function<std::string(const std::string& cmd, const std::string& peer)>;  // reply JSON

    MonitorServer(Options opts, SnapshotFn snapshot, CommandFn command);
    ~MonitorServer();
    MonitorServer(const MonitorServer&) = delete;
    MonitorServer& operator=(const MonitorServer&) = delete;

    void start();  // throws std::system_error if it cannot bind
    void stop();

    void publishLog(const char* level, const std::string& line);  // thread-safe, non-blocking
    uint16_t port() const { return port_; }
    std::size_t clientCount() const { return clientCount_; }

private:
    struct Client {
        int fd;
        std::string peer;
        std::string in;
        std::string out;
        bool dead = false;
    };

    void run();
    void accept();
    void readFrom(Client& c);
    void send(Client& c, const std::string& line);
    bool flush(Client& c);  // false -> drop
    void broadcast(const std::string& line);

    Options opts_;
    SnapshotFn snapshot_;
    CommandFn command_;
    int listenFd_ = -1;
    uint16_t port_ = 0;
    std::vector<Client> clients_;
    std::atomic<std::size_t> clientCount_{0};

    struct LogLine { std::string json; };
    common::ThreadSafeQueue<LogLine> logs_{2000};
    std::mutex replayMutex_;
    std::deque<std::string> replay_;

    std::atomic<bool> running_{false};
    std::thread thread_;
};

}  // namespace krbot::core
