// Reference digests of a DEEP+ capture: replays it with the shared driver
// rules and prints one line per sequence epoch, plus a mid-epoch checkpoint
// line every N messages with --checkpoint-every N (see book_digest.hpp).
//
//   iex_digest data/iex/20241223_IEXTP1_DPLS1.0.pcap.gz [--out FILE] [--checkpoint-every N]

#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <string>

#include "pkmn_md/book_digest.hpp"

int main(int argc, char** argv) {
    std::string path, out_path;
    std::uint64_t every = 0;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "--out" && i + 1 < argc) {
            out_path = argv[++i];
        } else if (a == "--checkpoint-every" && i + 1 < argc) {
            const char* v = argv[++i];
            char* end = nullptr;
            errno = 0;
            every = std::strtoull(v, &end, 10);
            if (*v == '\0' || *v == '-' || *end != '\0' || errno != 0 || every == 0) {
                path.clear();
                break;
            }
        } else if (path.empty() && a.rfind("--", 0) != 0) {
            path = a;
        } else {
            path.clear();
            break;
        }
    }
    if (path.empty()) {
        std::fprintf(stderr, "usage: %s <dplus.pcap.gz> [--out FILE] [--checkpoint-every N]\n", argv[0]);
        return 2;
    }
    try {
        const auto digests = pkmn::md::reference_digest_set(path, every);
        std::FILE* out = out_path.empty() ? stdout : std::fopen(out_path.c_str(), "w");
        if (out == nullptr) {
            std::fprintf(stderr, "cannot open %s\n", out_path.c_str());
            return 1;
        }
        pkmn::md::write_digest_set(out, digests);
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
