// Reference digests of a DEEP+ capture: replays it with the shared driver
// rules and prints one line per sequence epoch (see book_digest.hpp).
//
//   iex_digest data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz [--out FILE]

#include <cstdio>
#include <exception>
#include <string>

#include "pkmn_md/book_digest.hpp"

int main(int argc, char** argv) {
    std::string path, out_path;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "--out" && i + 1 < argc) {
            out_path = argv[++i];
        } else if (path.empty() && a.rfind("--", 0) != 0) {
            path = a;
        } else {
            path.clear();
            break;
        }
    }
    if (path.empty()) {
        std::fprintf(stderr, "usage: %s <dplus.pcap.gz> [--out FILE]\n", argv[0]);
        return 2;
    }
    try {
        const auto digests = pkmn::md::reference_digests(path);
        std::FILE* out = out_path.empty() ? stdout : std::fopen(out_path.c_str(), "w");
        if (out == nullptr) {
            std::fprintf(stderr, "cannot open %s\n", out_path.c_str());
            return 1;
        }
        pkmn::md::write_digests(out, digests);
        if (out != stdout && std::fclose(out) != 0) {
            std::fprintf(stderr, "write failed: %s\n", out_path.c_str());
            return 1;
        }
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        return 1;
    }
    return 0;
}
