// Monitor protocol: JSON helpers + a real MonitorServer on an ephemeral localhost port.
#include "common/Json.hpp"
#include "core/MonitorServer.hpp"

#include <arpa/inet.h>
#include <atomic>
#include <chrono>
#include <gtest/gtest.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>

using namespace krbot;
using namespace std::chrono_literals;
namespace json = common::json;

TEST(Json, QuoteEscapes) {
    EXPECT_EQ(json::quote("a\"b\\c\nd"), R"("a\"b\\c\nd")");
    EXPECT_EQ(json::quote(std::string("\x01", 1)), R"("\u0001")");
}

TEST(Json, StringField) {
    EXPECT_EQ(json::stringField(R"({"cmd":"estop"})", "cmd"), "estop");
    EXPECT_EQ(json::stringField(R"({ "x":"cmd", "cmd" : "ping" })", "cmd"), "ping");  // "cmd" as a value is skipped
    EXPECT_EQ(json::stringField(R"({"cmd":"a\"b"})", "cmd"), "a\"b");
    EXPECT_FALSE(json::stringField(R"({"cmd":5})", "cmd"));
    EXPECT_FALSE(json::stringField("not json", "cmd"));
}

namespace {
// Tiny blocking line client for the test.
struct LineClient {
    int fd = -1;
    std::string buf;
    explicit LineClient(uint16_t port) {
        fd = ::socket(AF_INET, SOCK_STREAM, 0);
        sockaddr_in a{};
        a.sin_family = AF_INET;
        a.sin_port = htons(port);
        ::inet_pton(AF_INET, "127.0.0.1", &a.sin_addr);
        if (::connect(fd, reinterpret_cast<sockaddr*>(&a), sizeof a) != 0) {
            ::close(fd);
            fd = -1;
        }
    }
    ~LineClient() {
        if (fd >= 0) ::close(fd);
    }
    void sendLine(const std::string& s) {
        const std::string l = s + "\n";
        ::send(fd, l.data(), l.size(), MSG_NOSIGNAL);
    }
    // Next line whose text contains `needle` (skipping others), within timeout.
    std::string waitFor(const std::string& needle, std::chrono::milliseconds timeout = 2s) {
        const auto end = std::chrono::steady_clock::now() + timeout;
        while (std::chrono::steady_clock::now() < end) {
            for (std::size_t nl; (nl = buf.find('\n')) != std::string::npos;) {
                std::string line = buf.substr(0, nl);
                buf.erase(0, nl + 1);
                if (line.find(needle) != std::string::npos) return line;
            }
            pollfd p{fd, POLLIN, 0};
            if (::poll(&p, 1, 50) > 0) {
                char tmp[4096];
                const ssize_t n = ::recv(fd, tmp, sizeof tmp, 0);
                if (n <= 0) return {};
                buf.append(tmp, static_cast<std::size_t>(n));
            }
        }
        return {};
    }
};
}  // namespace

TEST(MonitorServer, HelloReplayStatusLogAndEstop) {
    std::atomic<int> estops{0};
    core::MonitorServer::Options o;
    o.port = 0;  // ephemeral
    o.rateHz = 20;
    core::MonitorServer srv(
        o, [] { return std::string(R"("mode":"DISARMED")"); },
        [&](const std::string& cmd, const std::string&) -> std::string {
            if (cmd == "estop") {
                ++estops;
                return R"({"type":"ack","cmd":"estop"})";
            }
            return {};
        });
    srv.start();
    ASSERT_NE(srv.port(), 0);
    srv.publishLog("INFO", "before connect");  // must be replayed to a late client

    LineClient c(srv.port());
    ASSERT_GE(c.fd, 0);
    EXPECT_NE(c.waitFor(R"("type":"hello")"), "");
    EXPECT_NE(c.waitFor("before connect"), "");
    EXPECT_NE(c.waitFor(R"({"type":"status","mode":"DISARMED"})"), "");

    srv.publishLog("WARN", "live \"quoted\" line");
    EXPECT_NE(c.waitFor(R"(live \"quoted\" line)"), "");

    c.sendLine(R"({"cmd":"estop"})");
    EXPECT_NE(c.waitFor(R"("type":"ack")"), "");
    EXPECT_EQ(estops, 1);

    c.sendLine("garbage");
    EXPECT_NE(c.waitFor(R"("type":"error")"), "");
    c.sendLine(R"({"cmd":"arm"})");  // no remote arming, by design
    EXPECT_NE(c.waitFor("unknown command"), "");
    srv.stop();
}

TEST(MonitorServer, ClientDisconnectIsHandled) {
    core::MonitorServer::Options o;
    o.port = 0;
    core::MonitorServer srv(o, [] { return std::string(R"("x":1)"); }, {});
    srv.start();
    {
        LineClient c(srv.port());
        ASSERT_GE(c.fd, 0);
        EXPECT_NE(c.waitFor("hello"), "");
        EXPECT_EQ(srv.clientCount(), 1u);
    }  // closes
    for (int i = 0; i < 40 && srv.clientCount() != 0; ++i) std::this_thread::sleep_for(25ms);
    EXPECT_EQ(srv.clientCount(), 0u);
    srv.stop();
}
