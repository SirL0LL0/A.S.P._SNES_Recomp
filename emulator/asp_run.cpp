// asp_run — headless runner for the A.S.P. translation work, built on the Snaggletooth core.
//
//   asp_run <rom> <outdir> --frames N [--input script.txt] [--shot f1,f2,..] [--every K]
//           [--watch LO-HI ...] [--wram f1,f2,..] [--pcframe f1,f2,..]
//
// * --input   : lines "frame <n> [1|2] <buttons...>" (port 1 unless 2 is given), buttons = b y select start up down
//               left right a x l r | none. Same format as .snaginput without the port column
//               (a port column "1" is also accepted).
// * --shot    : save PNG screenshots at these frames; --every K saves one every K frames.
// * --watch   : SNES address range (hex, e.g. 858000-85FFFF) whose CPU data reads are logged
//               with the address of the instruction that made them -> reads.csv
// * --wram    : dump 128 KB WRAM at these frames (wram_<frame>.bin)
// * --vram    : dump VRAM/CGRAM/OAM + PPU registers at these frames (vram_/cgram_/oam_/ppu_<frame>)
// * --poke F:7EXXXX=AABB : write bytes into WRAM at frame F (exploration/cheat, e.g. advance the clock)
// * --vramwatch LO-HI : who writes VRAM words LO..HI (CPU instruction or DMA source) -> vramwrites.csv
// * --dmalog  : log every general-purpose DMA transfer (frame, channel, source, bytes, B-bus, VRAM word) -> dma.csv
// * --pcframe : list every instruction address executed during these frames (pcs_<frame>.txt)
#include <cstdint>
#include <span>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <tuple>
#include <vector>

#include "lodepng.h"
#include "snaggletooth/snes/cartridge.h"
#include "snaggletooth/snes/snes.h"
#include "snaggletooth/snes/video_frame.h"

using namespace snaggletooth;

struct Range { uint32_t lo, hi; };

static std::vector<uint32_t> parseList(const std::string& s) {
  std::vector<uint32_t> v; std::stringstream ss(s); std::string t;
  while (std::getline(ss, t, ',')) if (!t.empty()) v.push_back(std::stoul(t));
  return v;
}

struct Watcher final : BusObserver {
  std::vector<Range> ranges;
  uint32_t opPc = 0;
  uint32_t frame = 0;
  std::set<uint32_t> pcFrames; std::map<uint32_t, std::set<uint32_t>> pcs;
  // (instruction address, data address) -> (first frame, count)
  std::map<std::pair<uint32_t, uint32_t>, std::pair<uint32_t, uint32_t>> hits;
  void access(const BusAccess& a) override {
    if (dmaLog && a.source == AccessSource::Dma) { if (a.write) dmaWrite(a); else dmaRead(a); }
    if (a.source == AccessSource::Dma && !a.write && open_[a.channel & 7] == false) lastDmaSrc[a.channel & 7] = a.address;
    if (a.source == AccessSource::Dma && !a.write && dmaLog) lastDmaSrc[a.channel & 7] = cur[a.channel & 7].src;
    if (a.write && vwLo <= vwHi) vramWrite(a);
    if (a.source != AccessSource::Cpu && a.source != AccessSource::Dma) return;
    if (a.source == AccessSource::Cpu && a.kind == CycleKind::OpcodeFetch) { opPc = a.address; if (pcFrames.count(frame)) pcs[frame].insert(opPc); return; }
    if (a.write) return;
    if (a.source == AccessSource::Cpu && a.kind != CycleKind::DataRead) return;
    uint32_t ad = a.address;
    for (auto& r : ranges) if (ad >= r.lo && ad <= r.hi) {
      uint32_t who = a.source == AccessSource::Dma ? (0xD0000000u | a.channel) : opPc;
      auto& h = hits[{who, ad}];
      if (h.second == 0) h.first = frame;
      ++h.second;
      break;
    }
  }
  void internal(uint32_t, std::optional<CycleKind>) override {}
  // ---- VRAM write watch ----
  uint32_t vwLo = 1, vwHi = 0;
  struct VW { uint32_t first, count, lo, hi; };
  std::map<std::pair<uint32_t, int>, VW> vw;   // (pc or DMA src bank marker, channel) -> stats
  uint32_t lastDmaSrc[8] = {};
  void vramWrite(const BusAccess& a) {
    if (!a.landed) return;
    uint32_t wd = *a.landed;
    if ((a.address & 0xFFFF) != 0x2118 && (a.address & 0xFFFF) != 0x2119) return;
    if (wd < vwLo || wd > vwHi) return;
    uint32_t who = a.source == AccessSource::Dma ? (0xD0000000u | (lastDmaSrc[a.channel & 7] & 0xFFFFFF)) : opPc;
    auto& v = vw[{who, a.source == AccessSource::Dma ? (int)a.channel : -1}];
    if (v.count == 0) { v.first = frame; v.lo = wd; v.hi = wd; }
    ++v.count; if (wd < v.lo) v.lo = wd; if (wd > v.hi) v.hi = wd;
  }
  // ---- DMA log ----
  bool dmaLog = false;
  struct Dma { uint32_t frame, ch, src, last, count; int bb; int landed; };
  std::vector<Dma> dmas; Dma cur[8]; bool open_[8] = {};
  void dmaRead(const BusAccess& a) {
    unsigned c = a.channel & 7;
    if (open_[c] && cur[c].frame == frame && (a.address == cur[c].last + 1 || a.address == cur[c].last)) {
      cur[c].last = a.address; cur[c].count++; return;
    }
    if (open_[c]) dmas.push_back(cur[c]);
    cur[c] = {frame, c, a.address, a.address, 1, -1, -1}; open_[c] = true;
  }
  void dmaWrite(const BusAccess& a) {
    unsigned c = a.channel & 7;
    if (!open_[c]) return;
    if (cur[c].bb < 0) cur[c].bb = a.address & 0xFFFF;
    if (cur[c].landed < 0 && a.landed) cur[c].landed = *a.landed;
  }
};

struct Frames final : FrameObserver {
  uint32_t n = 0;
  std::set<uint32_t> shots; uint32_t every = 0;
  std::string out;
  void frame(const VideoFrame& f) override {
    if (shots.count(n) || (every && n % every == 0)) {
      char name[64]; std::snprintf(name, sizeof name, "/shot_%06u.png", n);
      std::vector<unsigned char> px(f.pixels.begin(), f.pixels.begin() + f.width * f.height * 4);
      for (size_t i = 3; i < px.size(); i += 4) px[i] = 255;
      lodepng::encode(out + name, px, f.width, f.height);
    }
    ++n;
  }
};

int main(int argc, char** argv) {
  if (argc < 3) { std::cerr << "usage: asp_run <rom> <outdir> --frames N [...]\n"; return 2; }
  std::string romPath = argv[1], out = argv[2];
  uint32_t frames = 600; std::string inputPath; std::set<uint32_t> shots, wramAt, vramAt; uint32_t every = 0;
  Watcher w;
  std::multimap<uint32_t, std::pair<uint32_t, std::vector<uint8_t>>> pokes;
  for (int i = 3; i < argc; ++i) {
    std::string a = argv[i];
    auto next = [&] { return std::string(argv[++i]); };
    if (a == "--frames") frames = std::stoul(next());
    else if (a == "--input") inputPath = next();
    else if (a == "--shot") for (auto f : parseList(next())) shots.insert(f);
    else if (a == "--every") every = std::stoul(next());
    else if (a == "--pcframe") for (auto f : parseList(next())) w.pcFrames.insert(f);
    else if (a == "--dmalog") w.dmaLog = true;
    else if (a == "--poke") {        // frame:7EXXXX=AABB..
      std::string r = next(); auto c = r.find(':'), e = r.find('=');
      uint32_t fr = std::stoul(r.substr(0, c)), ad = std::stoul(r.substr(c + 1, e - c - 1), nullptr, 16);
      std::vector<uint8_t> v; std::string hx = r.substr(e + 1);
      for (size_t k = 0; k + 1 < hx.size() + 1 && k < hx.size(); k += 2) v.push_back((uint8_t)std::stoul(hx.substr(k, 2), nullptr, 16));
      pokes.insert({fr, {ad, v}});
    }
    else if (a == "--vramwatch") {
      std::string r = next(); auto d = r.find('-');
      w.vwLo = std::stoul(r.substr(0, d), nullptr, 16); w.vwHi = std::stoul(r.substr(d + 1), nullptr, 16);
    }
    else if (a == "--vram") for (auto f : parseList(next())) vramAt.insert(f);
    else if (a == "--wram") for (auto f : parseList(next())) wramAt.insert(f);
    else if (a == "--watch") {
      std::string r = next(); auto d = r.find('-');
      w.ranges.push_back({(uint32_t)std::stoul(r.substr(0, d), nullptr, 16),
                          (uint32_t)std::stoul(r.substr(d + 1), nullptr, 16)});
    }
  }
  std::filesystem::create_directories(out);
  std::ifstream rf(romPath, std::ios::binary);
  if (!rf) { std::cerr << "cannot open " << romPath << "\n"; return 1; }
  std::vector<uint8_t> rom((std::istreambuf_iterator<char>(rf)), {});
  if (rom.size() % 0x8000 == 0x200) rom.erase(rom.begin(), rom.begin() + 0x200);

  // input script: frame -> pad, per port ("frame N [1|2] buttons...", port 1 by default)
  std::map<uint32_t, Joypad> script, script2;
  bool usePort2 = false;
  if (!inputPath.empty()) {
    std::ifstream in(inputPath); std::string line;
    while (std::getline(in, line)) {
      auto sc = line.find(';'); if (sc != std::string::npos) line.resize(sc);
      std::stringstream ss(line); std::string kw; uint32_t fr;
      if (!(ss >> kw >> fr) || kw != "frame") continue;
      Joypad p; std::string b; int port = 1; bool first = true;
      while (ss >> b) {
        if (first && (b == "1" || b == "2")) { port = b[0] - '0'; first = false; continue; }
        first = false;
        if (b == "none") continue;
        if (auto bt = buttonFromName(b)) p.hold(*bt, true);
        else std::cerr << "unknown button " << b << "\n";
      }
      if (port == 2) { script2[fr] = p; usePort2 = true; } else script[fr] = p;
    }
  }

  Snes machine(SnesConfig{.rom = rom, .region = Region::Ntsc});
  Frames fo; fo.shots = shots; fo.every = every; fo.out = out;
  machine.setFrameObserver(&fo);
  if (!w.ranges.empty() || !w.pcFrames.empty() || w.dmaLog || w.vwLo <= w.vwHi) machine.setObserver(&w);
  machine.setJoypad(JoypadPort::One, Joypad{});
  const uint64_t perFrame = consoleClock(Region::Ntsc).masterCyclesPerFrame;
  Joypad cur{};
  uint32_t last = UINT32_MAX;
  while (fo.n < frames) {
    if (fo.n != last) {
      last = fo.n; w.frame = fo.n;
      auto it = script.upper_bound(fo.n);
      if (it != script.begin()) { --it; cur = it->second; }
      machine.setJoypad(JoypadPort::One, cur);
      if (usePort2) {
        auto it3 = script2.upper_bound(fo.n);
        Joypad c2{}; if (it3 != script2.begin()) { --it3; c2 = it3->second; }
        machine.setJoypad(JoypadPort::Two, c2);
      }
      auto pr = pokes.equal_range(fo.n);
      if (pr.first != pr.second) {
        SnesState st = machine.state();
        for (auto it2 = pr.first; it2 != pr.second; ++it2) {
          uint32_t ad = it2->second.first;
          for (size_t k = 0; k < it2->second.second.size(); ++k)
            st.wram[((ad - 0x7E0000) + k) & 0x1FFFF] = it2->second.second[k];
        }
        machine.restore(st);
      }
      if (vramAt.count(fo.n)) {
        char name[80];
        auto dump = [&](const char* what, std::span<const std::uint8_t> d) {
          std::snprintf(name, sizeof name, "/%s_%06u.bin", what, fo.n);
          std::ofstream(out + name, std::ios::binary).write((const char*)d.data(), d.size());
        };
        dump("vram", machine.vram()); dump("cgram", machine.cgram()); dump("oam", machine.oam());
        const auto& p = machine.state().ppu;
        std::snprintf(name, sizeof name, "/ppu_%06u.txt", fo.n);
        std::ofstream o(out + name);
        char b[512];
        std::snprintf(b, sizeof b,
          "inidisp=%02X bgmode=%02X bg1sc=%02X bg2sc=%02X bg3sc=%02X bg4sc=%02X bg12nba=%02X bg34nba=%02X "
          "objsel=%02X tm=%02X setini=%02X\nbg1=%d,%d bg2=%d,%d bg3=%d,%d bg4=%d,%d\n",
          p.inidisp, p.bgmode, p.bg1sc, p.bg2sc, p.bg3sc, p.bg4sc, p.bg12nba, p.bg34nba, p.objsel, p.tm, p.setini,
          p.bg1hofs & 0x3FF, p.bg1vofs & 0x3FF, p.bg2hofs & 0x3FF, p.bg2vofs & 0x3FF,
          p.bg3hofs & 0x3FF, p.bg3vofs & 0x3FF, p.bg4hofs & 0x3FF, p.bg4vofs & 0x3FF);
        o << b;
      }
      if (wramAt.count(fo.n)) {
        char name[64]; std::snprintf(name, sizeof name, "/wram_%06u.bin", fo.n);
        auto st = machine.state();
        std::ofstream(out + name, std::ios::binary).write((const char*)st.wram.data(), st.wram.size());
      }
    }
    machine.run(perFrame / 8);
    (void)machine.takeFrames();
  }
  if (!w.ranges.empty()) {
    std::ofstream csv(out + "/reads.csv");
    csv << "who,addr,first_frame,count\n";
    for (auto& [k, v] : w.hits) {
      char b[96];
      if ((k.first & 0xF0000000u) == 0xD0000000u)
        std::snprintf(b, sizeof b, "DMA%u,%06X,%u,%u\n", k.first & 7, k.second, v.first, v.second);
      else
        std::snprintf(b, sizeof b, "%06X,%06X,%u,%u\n", k.first, k.second, v.first, v.second);
      csv << b;
    }
  }
  if (w.dmaLog) {
    for (unsigned c = 0; c < 8; ++c) if (w.open_[c]) w.dmas.push_back(w.cur[c]);
    std::ofstream o(out + "/dma.csv");
    o << "frame,ch,src,count,bbus,vram_word\n";
    for (auto& d : w.dmas) {
      char b[96];
      std::snprintf(b, sizeof b, "%u,%u,%06X,%u,%04X,%d\n", d.frame, d.ch, d.src, d.count, d.bb < 0 ? 0 : d.bb, d.landed);
      o << b;
    }
  }
  if (w.vwLo <= w.vwHi) {
    std::ofstream o(out + "/vramwrites.csv");
    o << "who,channel,first_frame,count,word_lo,word_hi\n";
    for (auto& [k, v] : w.vw) {
      char b[128];
      if ((k.first & 0xF0000000u) == 0xD0000000u)
        std::snprintf(b, sizeof b, "DMA:%06X,%d,%u,%u,%04X,%04X\n", k.first & 0xFFFFFF, k.second, v.first, v.count, v.lo, v.hi);
      else
        std::snprintf(b, sizeof b, "%06X,-1,%u,%u,%04X,%04X\n", k.first, v.first, v.count, v.lo, v.hi);
      o << b;
    }
  }
  for (auto& [f, S] : w.pcs) {
    char name[64]; std::snprintf(name, sizeof name, "/pcs_%06u.txt", f);
    std::ofstream o(out + name);
    for (auto p : S) { char b[16]; std::snprintf(b, sizeof b, "%06X\n", p); o << b; }
  }
  std::cerr << "ran " << fo.n << " frames\n";
  return 0;
}
