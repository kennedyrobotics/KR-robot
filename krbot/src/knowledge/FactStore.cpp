#include "knowledge/FactStore.hpp"

#include "common/Log.hpp"

namespace krbot::knowledge {

using namespace std::chrono_literals;
static constexpr auto kTag = "L3.facts";

FactStore::FactStore() = default;
FactStore::~FactStore() { stop(); }

void FactStore::start() {
    if (running_.exchange(true)) return;
    thread_ = std::thread([this] { run(); });
}

void FactStore::stop() {
    if (!running_.exchange(false)) return;
    commands_.close();
    if (thread_.joinable()) thread_.join();
    deltas_.close();
}

void FactStore::upsert(Fact f) { commands_.push(Upsert{std::move(f)}); }
void FactStore::add(Fact f) { commands_.push(Add{std::move(f)}); }
void FactStore::retract(FactId id) { commands_.push(Retract{id}); }

std::vector<Fact> FactStore::query(FactQuery q) {
    auto p = std::make_shared<std::promise<std::vector<Fact>>>();
    auto fut = p->get_future();
    if (!commands_.push(Query{std::move(q), p})) return {};
    if (fut.wait_for(1s) != std::future_status::ready) return {};
    return fut.get();
}

void FactStore::publish(FactDelta d) {
    if (!deltas_.push(std::move(d))) KLOG_WARN(kTag, "delta queue full — L4 not keeping up, delta dropped");
}

void FactStore::run() {
    auto nextExpiry = Clock::now() + 100ms;
    while (running_ || commands_.size() > 0) {
        if (auto cmd = commands_.popFor(50ms)) {
            std::visit(
                [this](auto& c) {
                    using T = std::decay_t<decltype(c)>;
                    if constexpr (std::is_same_v<T, Upsert>) {
                        publish(table_.upsert(std::move(c.f)).second);
                    } else if constexpr (std::is_same_v<T, Add>) {
                        publish(table_.add(std::move(c.f)).second);
                    } else if constexpr (std::is_same_v<T, Retract>) {
                        if (auto d = table_.retract(c.id)) publish(std::move(*d));
                    } else {
                        c.reply->set_value(table_.query(c.q));
                    }
                },
                *cmd);
            size_ = table_.size();
        } else if (commands_.closed()) {
            break;
        }
        if (const auto now = Clock::now(); now >= nextExpiry) {
            for (auto& d : table_.expire(now)) publish(std::move(d));
            size_ = table_.size();
            nextExpiry = now + 100ms;
        }
    }
}

}  // namespace krbot::knowledge
