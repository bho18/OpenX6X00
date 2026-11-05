// SPDX-License-Identifier: LGPL-2.1-or-later
#include "src/cfg/parameter.h"
#include "src/cfg/computed_parameter.h"
#include <cassert>
#include <thread>
#include <atomic>
#include <cstdio>
extern "C" bool scheduler_on_gui_thread(void) { return true; }
struct Sink : WriteSink {
    std::atomic_uint writes{0};
    void write(const StorageKey &, int32_t) override { ++writes; }
    void write(const StorageKey &, float) override { ++writes; }
    void write(const StorageKey &, const std::string &) override { ++writes; }
};
int main() {
    Sink sink;
    unsigned validations = 0, reverses = 0;
    Parameter<int32_t> p("test", 7, [&](int32_t v) {
        ++validations;
        assert(!fd_writer_reserve()); /* acquisition cannot race a validator or callback */
        return v;
    }, StorageType::GLOBAL, sink);
    ComputedParameter<int32_t> computed([&] { return p.get(); }, [&](const int32_t &v) {
        ++reverses; p.set(v);
    });
    computed.set(8);
    assert(p.get() == 8 && validations == 1 && reverses == 1 && sink.writes == 1);
    assert(fd_writer_reserve());
    std::thread remote([&] {
        for (int i = 0; i < 10000; ++i) { p.set(99); p.set_quiet(100); computed.set(101); }
    });
    remote.join();
    assert(p.get() == 8 && computed.get() == 8 && validations == 1 && reverses == 1 && sink.writes == 1);
    fd_writer_release();
    computed.set(9);
    assert(p.get() == 9 && validations == 2 && reverses == 2 && sink.writes == 2);
    puts("PASS: diagnostic reservation excludes validation, computed reverse, observers and deferred writes");
}
