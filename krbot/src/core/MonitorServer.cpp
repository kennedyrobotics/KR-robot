#include "core/MonitorServer.hpp"

#include "common/Json.hpp"

#include <algorithm>
#include <format>

#include <arpa/inet.h>
#include <cerrno>
#include <chrono>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <system_error>
#include <unistd.h>

namespace krbot::core {

namespace json = common::json;
using Clock = std::chrono::steady_clock;

namespace {
constexpr std::size_t kMaxLine = 4096;          // inbound command line cap
constexpr std::size_t kMaxOutBuffer = 1 << 20;  // a client this far behind is dropped
constexpr const char* kVersion = "0.1.0";

void setNonBlocking(int fd) { ::fcntl(fd, F_SETFL, ::fcntl(fd, F_GETFL) | O_NONBLOCK); }
}  // namespace

MonitorServer::MonitorServer(Options opts, SnapshotFn snapshot, CommandFn command)
    : opts_(std::move(opts)), snapshot_(std::move(snapshot)), command_(std::move(command)) {}

MonitorServer::~MonitorServer() { stop(); }

void MonitorServer::start() {
    if (running_) return;
    const int fd = ::socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) throw std::system_error(errno, std::generic_category(), "monitor socket");
    const int one = 1;
    ::setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(opts_.port);
    if (::inet_pton(AF_INET, opts_.bind.c_str(), &addr.sin_addr) != 1) {
        ::close(fd);
        throw std::system_error(EINVAL, std::generic_category(), "monitor bind address " + opts_.bind);
    }
    if (::bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0 || ::listen(fd, 8) != 0) {
        const int e = errno;
        ::close(fd);
        throw std::system_error(e, std::generic_category(),
                                "monitor bind " + opts_.bind + ":" + std::to_string(opts_.port));
    }
    socklen_t len = sizeof addr;
    ::getsockname(fd, reinterpret_cast<sockaddr*>(&addr), &len);
    port_ = ntohs(addr.sin_port);
    setNonBlocking(fd);
    listenFd_ = fd;
    running_ = true;
    thread_ = std::thread([this] { run(); });
}

void MonitorServer::stop() {
    if (!running_.exchange(false)) return;
    if (thread_.joinable()) thread_.join();
    for (auto& c : clients_) ::close(c.fd);
    clients_.clear();
    clientCount_ = 0;
    if (listenFd_ >= 0) ::close(listenFd_);
    listenFd_ = -1;
}

void MonitorServer::publishLog(const char* level, const std::string& line) {
    std::string j = std::format(R"({{"type":"log","level":{},"line":{}}})", json::quote(level), json::quote(line));
    {
        std::lock_guard lock(replayMutex_);
        replay_.push_back(j);
        while (replay_.size() > opts_.replayLines) replay_.pop_front();
    }
    logs_.push(LogLine{std::move(j)});  // full queue -> line dropped (never block the logger)
}

void MonitorServer::send(Client& c, const std::string& line) {
    c.out += line;
    c.out += '\n';
}

bool MonitorServer::flush(Client& c) {
    if (c.dead) return false;
    while (!c.out.empty()) {
        const ssize_t n = ::send(c.fd, c.out.data(), c.out.size(), MSG_NOSIGNAL);
        if (n < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) break;
            if (errno == EINTR) continue;
            return false;
        }
        c.out.erase(0, static_cast<std::size_t>(n));
    }
    return c.out.size() <= kMaxOutBuffer;
}

void MonitorServer::broadcast(const std::string& line) {
    for (auto& c : clients_) send(c, line);
}

void MonitorServer::accept() {
    sockaddr_in peer{};
    socklen_t len = sizeof peer;
    const int fd = ::accept4(listenFd_, reinterpret_cast<sockaddr*>(&peer), &len, SOCK_NONBLOCK | SOCK_CLOEXEC);
    if (fd < 0) return;
    if (clients_.size() >= opts_.maxClients) {
        static const std::string busy = R"({"type":"error","msg":"too many monitor clients"})" "\n";
        ::send(fd, busy.data(), busy.size(), MSG_NOSIGNAL);
        ::close(fd);
        return;
    }
    const int one = 1;
    ::setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    char ip[INET_ADDRSTRLEN] = {};
    ::inet_ntop(AF_INET, &peer.sin_addr, ip, sizeof ip);
    Client c{fd, std::format("{}:{}", ip, ntohs(peer.sin_port)), {}, {}, false};
    send(c, std::format(R"({{"type":"hello","server":"krbot","version":"{}","protocol":1,"commands":["estop","ping"]}})",
                        kVersion));
    {
        std::lock_guard lock(replayMutex_);
        for (const auto& l : replay_) send(c, l);
    }
    clients_.push_back(std::move(c));
    clientCount_ = clients_.size();
}

void MonitorServer::readFrom(Client& c) {
    char buf[1024];
    while (true) {
        const ssize_t n = ::recv(c.fd, buf, sizeof buf, 0);
        if (n > 0) {
            c.in.append(buf, static_cast<std::size_t>(n));
            continue;
        }
        if (n == 0 || (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)) {
            c.in.clear();
            c.dead = true;  // peer closed or socket error
            return;
        }
        break;
    }
    std::size_t nl;
    while ((nl = c.in.find('\n')) != std::string::npos) {
        const std::string line = c.in.substr(0, nl);
        c.in.erase(0, nl + 1);
        const auto cmd = json::stringField(line, "cmd");
        if (!cmd) {
            send(c, R"({"type":"error","msg":"expected {\"cmd\":\"...\"}"})");
            continue;
        }
        const auto reply = command_ ? command_(*cmd, c.peer) : std::string{};
        send(c, reply.empty() ? std::string(R"({"type":"error","msg":"unknown command"})") : reply);
    }
    if (c.in.size() > kMaxLine) c.in.clear();
}

void MonitorServer::run() {
    const auto period = std::chrono::milliseconds(1000 / std::max(1, opts_.rateHz));
    auto nextSnapshot = Clock::now();
    std::vector<pollfd> pfds;
    while (running_) {
        pfds.clear();
        pfds.push_back({listenFd_, POLLIN, 0});
        for (const auto& c : clients_) pfds.push_back({c.fd, static_cast<short>(POLLIN | (c.out.empty() ? 0 : POLLOUT)), 0});
        const auto wait = std::chrono::duration_cast<std::chrono::milliseconds>(nextSnapshot - Clock::now()).count();
        ::poll(pfds.data(), pfds.size(), static_cast<int>(std::clamp<long long>(wait, 0, 100)));

        if (pfds[0].revents & POLLIN) accept();
        for (std::size_t i = 1; i < pfds.size() && i - 1 < clients_.size(); ++i)
            if (pfds[i].revents & (POLLIN | POLLHUP | POLLERR)) readFrom(clients_[i - 1]);

        for (auto& l : logs_.drain()) broadcast(l.json);
        if (Clock::now() >= nextSnapshot) {
            if (!clients_.empty() && snapshot_) broadcast(R"({"type":"status",)" + snapshot_() + "}");
            nextSnapshot = std::max(nextSnapshot + period, Clock::now());
        }

        for (auto it = clients_.begin(); it != clients_.end();) {
            if (!flush(*it)) {
                ::close(it->fd);
                it = clients_.erase(it);
            } else {
                ++it;
            }
        }
        clientCount_ = clients_.size();
    }
}

}  // namespace krbot::core
