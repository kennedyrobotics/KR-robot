#pragma once
// MPSC/MPMC queue used for every cross-layer hand-off (doc v3 §7: "one thread per layer
// connected by thread-safe queues"). close() wakes all waiters so threads can shut down.

#include <chrono>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <optional>
#include <vector>

namespace krbot::common {

template <typename T>
class ThreadSafeQueue {
public:
    explicit ThreadSafeQueue(std::size_t capacity = 0) : capacity_(capacity) {}

    // Returns false if closed, or if bounded and full (oldest is kept - callers decide policy).
    bool push(T value) {
        {
            std::lock_guard lock(m_);
            if (closed_ || (capacity_ && q_.size() >= capacity_)) return false;
            q_.push_back(std::move(value));
        }
        cv_.notify_one();
        return true;
    }

    std::optional<T> tryPop() {
        std::lock_guard lock(m_);
        if (q_.empty()) return std::nullopt;
        T v = std::move(q_.front());
        q_.pop_front();
        return v;
    }

    template <typename Rep, typename Period>
    std::optional<T> popFor(std::chrono::duration<Rep, Period> timeout) {
        std::unique_lock lock(m_);
        cv_.wait_for(lock, timeout, [&] { return closed_ || !q_.empty(); });
        if (q_.empty()) return std::nullopt;
        T v = std::move(q_.front());
        q_.pop_front();
        return v;
    }

    std::vector<T> drain() {
        std::lock_guard lock(m_);
        std::vector<T> out(std::make_move_iterator(q_.begin()), std::make_move_iterator(q_.end()));
        q_.clear();
        return out;
    }

    void close() {
        {
            std::lock_guard lock(m_);
            closed_ = true;
        }
        cv_.notify_all();
    }

    bool closed() const {
        std::lock_guard lock(m_);
        return closed_;
    }

    std::size_t size() const {
        std::lock_guard lock(m_);
        return q_.size();
    }

private:
    mutable std::mutex m_;
    std::condition_variable cv_;
    std::deque<T> q_;
    std::size_t capacity_;
    bool closed_ = false;
};

}  // namespace krbot::common
