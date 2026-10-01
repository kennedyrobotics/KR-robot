#include "driver/YahboomProtocol.hpp"

#include <charconv>
#include <format>

namespace krbot::driver::yahboom {

std::string formatCommand(std::string_view name, const std::vector<int>& args) {
    std::string out = std::format("${}:", name);
    for (std::size_t i = 0; i < args.size(); ++i) {
        if (i) out += ',';
        out += std::to_string(args[i]);
    }
    out += '#';
    return out;
}

std::string formatCommandFloat(std::string_view name, double arg) {
    return std::format("${}:{:.2f}#", name, arg);
}

std::vector<std::string> profileCommands(const MotorProfile& p) {
    std::vector<std::string> cmds{formatCommand("mtype", std::vector<int>{p.mtype})};
    if (p.phase) cmds.push_back(formatCommand("mphase", std::vector<int>{*p.phase}));
    if (p.line) cmds.push_back(formatCommand("mline", std::vector<int>{*p.line}));
    if (p.wheelDiaMm) cmds.push_back(formatCommandFloat("wdiameter", *p.wheelDiaMm));
    if (p.deadzone) cmds.push_back(formatCommand("deadzone", std::vector<int>{*p.deadzone}));
    return cmds;
}

std::vector<std::string> FrameParser::feed(std::string_view bytes) {
    buf_.append(bytes);
    std::vector<std::string> frames;
    while (true) {
        const auto start = buf_.find('$');
        if (start == std::string::npos) {
            buf_.clear();
            break;
        }
        const auto end = buf_.find('#', start);
        if (end == std::string::npos) {
            buf_.erase(0, start);
            if (buf_.size() > kMaxPending) buf_.clear();
            break;
        }
        std::string body = buf_.substr(start + 1, end - start - 1);
        // trim whitespace
        const auto b = body.find_first_not_of(" \r\n\t");
        const auto e = body.find_last_not_of(" \r\n\t");
        frames.push_back(b == std::string::npos ? std::string{} : body.substr(b, e - b + 1));
        buf_.erase(0, end + 1);
    }
    return frames;
}

namespace {
std::vector<std::string_view> splitCsv(std::string_view s) {
    std::vector<std::string_view> out;
    while (!s.empty()) {
        const auto comma = s.find(',');
        auto tok = s.substr(0, comma);
        while (!tok.empty() && tok.front() == ' ') tok.remove_prefix(1);
        while (!tok.empty() && tok.back() == ' ') tok.remove_suffix(1);
        if (!tok.empty()) out.push_back(tok);
        if (comma == std::string_view::npos) break;
        s.remove_prefix(comma + 1);
    }
    return out;
}

template <typename T>
bool parseNumbers(std::string_view rest, std::array<T, 4>& dst) {
    const auto toks = splitCsv(rest);
    if (toks.empty()) return false;
    std::array<T, 4> tmp{};
    for (std::size_t i = 0; i < toks.size() && i < 4; ++i) {
        T v{};
        const auto [p, ec] = std::from_chars(toks[i].data(), toks[i].data() + toks[i].size(), v);
        if (ec != std::errc{} || p != toks[i].data() + toks[i].size()) return false;
        tmp[i] = v;
    }
    dst = tmp;
    return true;
}
}  // namespace

bool parseReport(std::string_view frame, Telemetry& telem) {
    const auto colon = frame.find(':');
    if (colon == std::string_view::npos) return false;
    const auto key = frame.substr(0, colon);
    const auto rest = frame.substr(colon + 1);
    bool ok = false;
    if (key == "MAll")
        ok = parseNumbers(rest, telem.totalPulses);
    else if (key == "MTEP")
        ok = parseNumbers(rest, telem.pulses10ms);
    else if (key == "MSPD")
        ok = parseNumbers(rest, telem.speedMmS);
    if (ok) telem.any = true;
    return ok;
}

}  // namespace krbot::driver::yahboom
