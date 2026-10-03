/*
 * A.S.P. Air Strike Patrol ITA — per-game runtime glue.
 *
 * THIS FILE IS THE PORT. Everything else the scaffold produced is layout,
 * build wiring, and packaging; this is where the actual work happens.
 *
 * The framework does not, and cannot, drive an arbitrary SNES game on its
 * own. Two responsibilities always land on the game host:
 *
 *   1. Deciding what "one frame" means for THIS title, and returning from
 *      run_frame() at that boundary. A real ROM's reset vector never
 *      returns — it enters a main loop that waits on vblank — so somebody
 *      has to choose the yield point.
 *   2. Delivering NMI/IRQ at the hardware edge (see
 *      snesrecomp/docs/FRAME_MODEL_HOSTS.md).
 *
 * What follows is the shape every working port in this ecosystem converged
 * on, built from documented bridge entry points:
 *
 *      boot from the reset vector
 *      -> deliver NMI at the vblank edge, gated on NMITIMEN
 *      -> run the guest in slices until it parks or the frame's clock is out
 *      -> service a raster IRQ whenever the comparator asserts
 *      -> rasterize the field with HDMA per line
 *
 * It is a starting point, not a finished port: a title with unusual timing
 * will need the slice loop tightened (MetalWarriorsSNESRecomp's src/mw_rtl.c
 * and GundamWingEndlessDuelSNESRecomp's src/game_rtl.c are the two most
 * developed examples, and the second walks the beam explicitly while the CPU
 * is parked). What it will NOT do is sit at a black screen doing nothing,
 * which is what a driver that delivers no interrupt always does.
 *
 * Why NMI is not gated on WAI: an earlier version of this template delivered
 * an interrupt only when interp_bridge_lle_took_wai() was true. Real titles
 * overwhelmingly wait on a WRAM flag set by their own NMI handler, not on
 * WAI — Super Metroid spins `LDA $05B4 / BNE`, Zelda 3 on $0012, Super Mario
 * World on $0010 — so that branch never fired and the guest parked forever
 * on its first vblank wait. Quiescence on a read-only cycle IS the signal
 * that only an interrupt can make progress; that is the edge to act on.
 */

#include "game_rtl.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "common_cpu_infra.h"
#include "common_rtl.h"          /* SimpleHdma_*, g_snesrecomp_last_hdmaen */
#include "cpu_state.h"
#include "snes/cart.h"
#include "snes/dma.h"
#include "snes/interp_bridge.h"
#include "snes/ppu.h"
#include "snes/snes.h"

extern CpuState g_cpu;
extern int snes_frame_counter;
extern Ppu *g_ppu;

/* One NTSC frame: 262 scanlines x 1364 master clocks. Bounds a productive
 * MMIO loop so it cannot run across several vblanks atomically. */
#define GAME_MASTER_CYCLES_PER_FRAME 357368ull

/* How many times a frame may re-enter the guest after it parks on a poll.
 * Each slice ends at a deterministic read-only cycle or at an IRQ; the bound
 * only stops a pathological loop from spinning the host. */
#define GAME_MAX_SLICES_PER_FRAME 64

/* 0 until the first frame has booted from the reset vector. */
static uint32_t g_resume_pc;

static uint32_t read_vector(uint32_t addr)
{
    /* Read through the guest bus so a mapper or coprocessor window resolves
     * the same way the CPU sees it. */
    uint32_t lo = snes_read(g_snes, addr);
    uint32_t hi = snes_read(g_snes, addr + 1u);
    return (hi << 8) | lo;
}

static uint32_t reset_vector(void) { return read_vector(0x00FFFCu); }
static uint32_t nmi_vector(void)   { return read_vector(0x00FFEAu); }
static uint32_t irq_vector(void)   { return read_vector(0x00FFEEu); }

/* Optional driver log: ASP_RTL_TRACE=1 logs every delivered interrupt;
 * ASP_RTL_TRACE=2 also logs each interpreted instruction inside IRQ handlers
 * (bounded by ASP_RTL_TRACE_MAX, default 4000 lines). */
static int rtl_trace_level(void)
{
    static int v = -1;
    if (v < 0) {
        const char *e = getenv("ASP_RTL_TRACE");
        v = (e && *e) ? atoi(e) : 0;
    }
    return v;
}
static int rtl_trace_enabled(void) { return rtl_trace_level() > 0; }

static long g_pc_trace_left = -1;
static void rtl_pc_hook(uint32_t pc24, int m_flag, int x_flag)
{
    if (g_pc_trace_left < 0) {
        const char *e = getenv("ASP_RTL_TRACE_MAX");
        g_pc_trace_left = (e && *e) ? atol(e) : 4000;
    }
    if (g_pc_trace_left == 0)
        return;
    g_pc_trace_left--;
    fprintf(stderr, "[asp_pc] f%d $%06X m%d x%d A=%04X X=%04X Y=%04X S=%04X P=%02X I=%d\n",
            snes_frame_counter, (unsigned)pc24, m_flag, x_flag, (unsigned)g_cpu.A,
            (unsigned)g_cpu.X, (unsigned)g_cpu.Y, (unsigned)g_cpu.S,
            (unsigned)g_cpu.P, (int)g_cpu._flag_I);
}

/* Where did the interrupt handler's RTI actually go?
 *
 * A.S.P. runs a PREEMPTIVE TASK SCHEDULER in its V-IRQ handler ($00:CC04,
 * reached through the WRAM trampoline at $0218): the handler saves the
 * interrupted task's S, loads another task's S from the table at $1F00 and
 * RTIs from THAT stack. The RTI therefore returns into a different task, not
 * to the instruction that was interrupted.
 *
 * The framework's interrupt bridge stops at the RTI and deliberately discards
 * the popped PC (snesrecomp interp_bridge.c, "host control flow deliberately
 * discards guest PC/PB"), so a generic driver would resume the OLD task's PC
 * on the NEW task's stack — the game then never leaves its forced-blank
 * loading state (black screen after the title menu).
 *
 * The popped frame is still in WRAM just below the post-RTI S: P at S-3,
 * PCL at S-2, PCH at S-1 and, in native mode, PB at S. Reading it back gives
 * the real continuation. When no task switch happened it equals the PC we
 * pushed, so this is exact for every interrupt, not just the scheduler's. */
static uint32_t rti_return_pc(void)
{
    const uint16_t s = g_cpu.S;
    uint32_t pcl, pch, pb;
    if (g_cpu.emulation) {
        /* 6502 mode: page-1 stack, 3-byte frame (P, PCL, PCH). */
        pcl = g_ram[0x0100u | (uint8_t)(s - 1u)];
        pch = g_ram[0x0100u | (uint8_t)s];
        pb = 0;
    } else {
        pcl = g_ram[(uint16_t)(s - 2u)];
        pch = g_ram[(uint16_t)(s - 1u)];
        pb  = g_ram[s];
    }
    return (pb << 16) | (pch << 8) | pcl;
}

/* After a host-delivered interrupt: a yield inside the handler resumes there;
 * a completed handler resumes wherever its RTI really went.
 *
 * interp_bridge_lle_resume_pc() is sticky — it still holds the previous
 * slice's park PC after a handler that ran cleanly to its RTI — so "nonzero"
 * cannot tell a yield from a completion. The resume-PC write counter can:
 * the handler yielded iff it recorded a new resume PC. */
static void game_after_interrupt(const char *what, uint32_t vector,
                                 uint64_t resume_writes_before)
{
    const uint32_t before = g_resume_pc;
    const int yielded = interp_bridge_resume_total() != resume_writes_before;
    const uint32_t resume = yielded ? interp_bridge_lle_resume_pc() : 0;
    if (resume)
        g_resume_pc = resume;
    else
        g_resume_pc = rti_return_pc();
    if (rtl_trace_enabled())
        fprintf(stderr, "[asp_rtl] %s vec=$%06X from=$%06X -> $%06X%s S=$%04X%s\n",
                what, (unsigned)vector, (unsigned)before, (unsigned)g_resume_pc,
                resume ? " (yield)" : "", (unsigned)g_cpu.S,
                (!resume && g_resume_pc != before) ? "  TASK SWITCH" : "");
}

/* Run one interrupt handler to its RTI, entered as hardware enters it: the
 * frame is pushed at the PC the guest was interrupted AT. */
static void game_run_interrupt(const char *what, uint32_t vector, uint64_t frame_end)
{
    const uint64_t writes = interp_bridge_resume_total();
    const int pc_trace = rtl_trace_level() >= 2 && what[0] == 'I';
    if (rtl_trace_enabled())
        fprintf(stderr, "[asp_rtl] f%d deliver %s at $%06X P=%02X flagI=%d S=$%04X beam=%u,%u\n",
                snes_frame_counter, what, (unsigned)g_resume_pc, (unsigned)g_cpu.P,
                (int)g_cpu._flag_I, (unsigned)g_cpu.S,
                (unsigned)g_snes->vPos, (unsigned)g_snes->hPos);
    cpu_push_interrupt_frame_at(&g_cpu, g_resume_pc);
    interp_bridge_set_master_deadline(frame_end);
    if (pc_trace)
        g_interp_bridge_pc_hook = rtl_pc_hook;
    (void)interp_bridge_run_interrupt(&g_cpu, vector);
    if (pc_trace)
        g_interp_bridge_pc_hook = NULL;
    /* Clearing matters: a deadline left armed stays true for every AOT block
     * prologue afterwards, which turns every compiled body into an immediate
     * yield-unwind. */
    interp_bridge_set_master_deadline(0);
    game_after_interrupt(what, vector, writes);
}

/* ── Beam-anchored frame ─────────────────────────────────────────────────
 *
 * A host frame is one field measured from VBLANK START (scanline 225, the
 * NMI edge) to the next vblank start, on the framework's own beam (g_snes
 * vPos/hPos, advanced from the CPU master clock).
 *
 * The scaffold's generic driver measured a fixed 357368 master clocks from
 * whatever the clock happened to read when the frame began. Boot starts the
 * beam at line 0, so every NMI landed ~225 lines early, and each frame's
 * overshoot carried into the next: measured on A.S.P., the NMI was delivered
 * at scanline 8 after boot, drifting to line 27 by frame 2400. Nothing that
 * depends on WHERE the beam is relative to the NMI — the V-IRQ the task
 * scheduler runs on (line 239, 14 lines into vblank), HVBJOY/RDNMI polls, how
 * much vblank time an NMI handler gets — can be right on such a clock.
 * A.S.P. lost exactly that race at the first mission briefing: the scheduler
 * IRQ landed before its first task was ready and the game sat in forced
 * blank for good. Recomputing the end of every frame from the beam removes
 * both the initial offset and the accumulated drift. */
#define GAME_LINE_CLOCKS   1364u
#define GAME_FIELD_LINES   262u
#define GAME_VBLANK_LINE   225u

/* Master clocks from the beam's current position to the next vblank start.
 * Called at a frame boundary, where the beam sits at (or a few clocks past)
 * line 225: that is a whole field away, never "now". */
static uint64_t cycles_to_next_vblank(void)
{
    const uint32_t v = g_snes->vPos, h = g_snes->hPos;
    uint32_t lines = (GAME_VBLANK_LINE + GAME_FIELD_LINES - v) % GAME_FIELD_LINES;
    /* On the vblank line itself (the normal case at a frame boundary) the
     * next edge is a whole field away. Measuring from the line START — not
     * from "now" — lands every frame on dot 0 of line 225; adding a fixed
     * field length instead kept the first frame's horizontal phase and let
     * the field's short scanline drift it (measured: dot 1248 by frame 1500). */
    if (lines == 0)
        lines = GAME_FIELD_LINES;
    return (uint64_t)lines * GAME_LINE_CLOCKS - h;
}

/* ── HDMA time ──────────────────────────────────────────────────────────
 * On hardware HDMA halts the CPU in the H-blank of every visible line it
 * transfers on: ~18 master clocks of per-line overhead when any channel is
 * live, 8 per active channel, 8 per byte moved, and 8 (+16 indirect) when a
 * channel loads a new table entry. The framework documents this cost (dma.c,
 * "Estimated master clocks one frame of HDMA steals from the CPU") but does
 * not charge it, so with HDMA on the guest gets ~5-10% more CPU per field
 * than the console gives it — fewer lag frames, and every beam race it runs
 * shifts. GameDrawPpuFrame walks the real tables; we count what it actually
 * transferred and charge it to the next field's CPU time, after the NMI
 * (vblank has no HDMA). ASP_HDMA_STEAL=0 turns it off for A/B comparison. */
static uint64_t g_hdma_debt;

static int hdma_steal_enabled(void)
{
    static int v = -1;
    if (v < 0) {
        const char *e = getenv("ASP_HDMA_STEAL");
        v = (e && *e == '0') ? 0 : 1;
    }
    return v;
}

static void charge_hdma_debt(uint64_t frame_end)
{
    uint64_t target;
    if (!g_hdma_debt)
        return;
    target = g_cpu.master_cycles + g_hdma_debt;
    if (target > frame_end)
        target = frame_end;
    g_hdma_debt = 0;
    g_cpu.master_cycles = target;
    snes_sync_master_clock(g_snes, target);
}

/* ── Idle loops ─────────────────────────────────────────────────────────
 * A.S.P. waits for the next field in tight WRAM polls:
 *
 *     LDA $0202 / CMP $0202 / BEQ -5     NMI counter (4 sites)
 *     LDA $06A9 / BEQ -5 (BNE/BPL/BMI)   flags its tasks set (9 sites)
 *
 * The framework's quiescent detector deliberately treats every read of the
 * low-WRAM mirror ($0000-$1FFF in the system banks) as dynamic, so it never
 * parks these, and the interpreter spins until the frame deadline: measured,
 * 46% of all interpreted instructions on a mission-1 route were this spin.
 *
 * With I=1 and a loop that only reads, nothing but the NMI can change WRAM
 * (no CPU writes, no DMA without a CPU write), so the field's remaining time
 * passes exactly as on hardware if we advance the clock to the field's end
 * the moment the loop is about to iterate again. A pre-opcode hook — the
 * bridge's supported extension point — does that at the loop head. With I=0
 * an IRQ could end the wait mid-field, so the loop is left to run.
 *
 * The sites are found by scanning the ROM at boot, not hard-coded. The SPC
 * port handshake ($2140 polls) is a device wait and is never matched.
 * ASP_IDLE_SKIP=0 disables this for A/B comparison. */
typedef struct {
    uint32_t pc24;      /* loop head: where the hook fires */
    uint16_t addr;      /* polled WRAM address (low mirror) */
    uint8_t cmp_a;      /* 1: LDA/CMP/BEQ — loops while WRAM == A */
    uint8_t branch;     /* LDA/Bxx form: F0 BEQ, D0 BNE, 10 BPL, 30 BMI */
} IdleLoop;

static IdleLoop g_idle[32];
static int g_idle_count;
static uint64_t g_idle_frame_end;
static uint64_t g_idle_skips;

static int idle_skip_enabled(void)
{
    static int v = -1;
    if (v < 0) {
        const char *e = getenv("ASP_IDLE_SKIP");
        v = (e && *e == '0') ? 0 : 1;
    }
    return v;
}

static void idle_hook(CpuState *cpu, uint32_t pc24)
{
    int i;
    if (!cpu->_flag_I || cpu->emulation || cpu->master_cycles >= g_idle_frame_end)
        return;
    for (i = 0; i < g_idle_count; i++) {
        const IdleLoop *l = &g_idle[i];
        uint32_t v, a;
        int loops;
        if ((l->pc24 & 0x7FFFFFu) != (pc24 & 0x7FFFFFu))
            continue;
        v = g_ram[l->addr];
        if (!cpu->m_flag)
            v |= (uint32_t)g_ram[(uint16_t)(l->addr + 1u)] << 8;
        if (l->cmp_a) {
            a = cpu->m_flag ? (cpu->A & 0xFFu) : cpu->A;
            loops = (v == a);                       /* CMP equal -> BEQ back */
        } else {
            const uint32_t sign = cpu->m_flag ? 0x80u : 0x8000u;
            switch (l->branch) {
            case 0xF0: loops = (v == 0); break;     /* BEQ */
            case 0xD0: loops = (v != 0); break;     /* BNE */
            case 0x10: loops = !(v & sign); break;  /* BPL */
            default:   loops = (v & sign) != 0; break; /* BMI */
            }
        }
        if (loops) {
            cpu->master_cycles = g_idle_frame_end;
            g_idle_skips++;
        }
        return;
    }
}

/* Scan the cartridge (LoROM, banks $80-$BF) for the two idle idioms on the
 * low-WRAM mirror and register a hook at each loop head. */
static void idle_loops_init(void)
{
    uint32_t bank, a, last_bank = 0xBF;
    if (!idle_skip_enabled())
        return;
    /* Only the banks the ROM really fills: past them LoROM mirrors repeat
     * the same code and would register every loop twice. */
    if (g_snes->cart && g_snes->cart->romSize >= 0x8000u)
        last_bank = 0x80u + (g_snes->cart->romSize >> 15) - 1u;
    if (last_bank > 0xBF)
        last_bank = 0xBF;
    for (bank = 0x80; bank <= last_bank && g_idle_count < 32; bank++) {
        for (a = 0x8000; a <= 0xFFF8 && g_idle_count < 32; a++) {
            const uint32_t pc = (bank << 16) | a;
            uint8_t b[8];
            int k;
            if (snes_read(g_snes, pc) != 0xAD)      /* LDA abs */
                continue;
            for (k = 0; k < 8; k++)
                b[k] = snes_read(g_snes, pc + (uint32_t)k);
            {
                const uint16_t addr = (uint16_t)(b[1] | (b[2] << 8));
                if (addr >= 0x2000)
                    continue;
                if (b[3] == 0xCD && b[4] == b[1] && b[5] == b[2] &&
                    b[6] == 0xF0 && b[7] == 0xFB) {
                    /* LDA addr / CMP addr / BEQ -5: the loop is CMP+BEQ. */
                    g_idle[g_idle_count++] = (IdleLoop){pc + 3u, addr, 1, 0xF0};
                } else if ((b[3] == 0xF0 || b[3] == 0xD0 || b[3] == 0x10 ||
                            b[3] == 0x30) && b[4] == 0xFB) {
                    /* LDA addr / Bxx -5: the loop is the whole pair. */
                    g_idle[g_idle_count++] = (IdleLoop){pc, addr, 0, b[3]};
                } else {
                    continue;
                }
                interp_bridge_add_pre_opcode_hook(g_idle[g_idle_count - 1].pc24, idle_hook);
            }
        }
    }
    if (rtl_trace_enabled())
        fprintf(stderr, "[asp_rtl] idle loops: %d registered\n", g_idle_count);
}

/* Run the guest through [now, frame_end) — the part of the field that is
 * CPU time — servicing IRQs as the beam latches them. */
static void game_run_slices(uint64_t frame_end)
{
    int slice;
    g_idle_frame_end = frame_end;
    for (slice = 0; slice < GAME_MAX_SLICES_PER_FRAME; slice++) {
        if (g_cpu.master_cycles >= frame_end)
            break;
        /* An IRQ latched (by the beam, inside the previous slice or in the
         * NMI) and the guest has interrupts enabled: take it at this
         * instruction boundary, before running anything else. */
        if (g_snes->inIrq && !g_cpu._flag_I) {
            game_run_interrupt("IRQ", irq_vector(), frame_end);
            continue;
        }
        interp_bridge_set_master_deadline(frame_end);
        interp_bridge_run_until_quiescent(&g_cpu, g_resume_pc);
        interp_bridge_set_master_deadline(0);
        {
            uint32_t resume = interp_bridge_lle_resume_pc();
            if (resume)
                g_resume_pc = resume;
        }
        if (rtl_trace_level() >= 4)
            fprintf(stderr, "[asp_rtl] f%d slice %d -> pc=$%06X master_left=%lld beam=%u,%u\n",
                    snes_frame_counter, slice, (unsigned)g_resume_pc,
                    (long long)(frame_end - g_cpu.master_cycles),
                    (unsigned)g_snes->vPos, (unsigned)g_snes->hPos);
        if (g_snes->inIrq && !g_cpu._flag_I)
            continue;                    /* serviced at the top of the loop */
        /* Parked on WAI with no interrupt pending: nothing more can happen
         * until the next interrupt edge. */
        if (interp_bridge_lle_took_wai())
            break;
        /* Parked on a stable poll: a loop that reads only WRAM/ROM (live MMIO
         * reads never qualify), so only an interrupt can change what it is
         * waiting for. A.S.P. spends most of every field like this — the
         * vblank wait `LDA $0202 / CMP $0202 / BEQ` at $8C:827F runs with
         * I=1 until the next NMI. Re-entering the guest just re-runs the spin
         * (measured: ~46% of all interpreted instructions in a mission-1
         * route). The bridge leaves the idle time to its owning scheduler
         * ("advances idle hardware to the next timer comparator or vblank"):
         * walk the beam toward the field's end. It stops early where a V-IRQ
         * latches; if the guest can take it, the top of the loop does. */
        if (interp_bridge_lle_took_quiescent()) {
            int walk;
            for (walk = 0; walk < 4 && g_snes->beamMasterLast < frame_end; walk++) {
                snes_sync_master_clock(g_snes, frame_end);
                if (g_snes->inIrq && !g_cpu._flag_I)
                    break;
            }
            if (g_snes->beamMasterLast > g_cpu.master_cycles)
                g_cpu.master_cycles = g_snes->beamMasterLast < frame_end
                                    ? g_snes->beamMasterLast : frame_end;
            if (!(g_snes->inIrq && !g_cpu._flag_I))
                break;                   /* nothing can happen before vblank */
        }
    }

    /* The guest parked (a vblank-wait spin or WAI) before the field ended.
     * On hardware it keeps spinning until the beam reaches vblank: advance
     * the clock — and with it the beam, which still latches any IRQ due on
     * the way — so the next NMI is delivered at scanline 225 and not early.
     * The frame-boundary APU sync (RtlRunFrame) catches the SPC up over the
     * same master-clock span. */
    if (g_cpu.master_cycles < frame_end) {
        g_cpu.master_cycles = frame_end;
        snes_sync_master_clock(g_snes, frame_end);
    }
}

void GameRunOneFrame(void)
{
    const int booting = (g_resume_pc == 0);
    uint64_t frame_end;

    /* Bring the beam up to the CPU clock before reading it. */
    snes_sync_master_clock(g_snes, g_cpu.master_cycles);

    {
        /* ASP_RTL_TRACE_FROM/TO: trace interpreted instructions of the main
         * program (not only interrupt handlers) inside a frame window. */
        static long from = -2, to = -2;
        if (from == -2) {
            const char *f = getenv("ASP_RTL_TRACE_FROM"), *t = getenv("ASP_RTL_TRACE_TO");
            from = (f && *f) ? atol(f) : -1;
            to = (t && *t) ? atol(t) : from;
        }
        g_interp_bridge_pc_hook = (from >= 0 && snes_frame_counter >= from &&
                                   snes_frame_counter <= to) ? rtl_pc_hook : NULL;
    }

    if (booting) {
        /* Power-on: the beam starts at line 0, so the first frame is the
         * partial field up to the first vblank. Reset runs with no interrupt
         * to deliver (no instruction stream exists yet, and NMI is off). */
        g_resume_pc = reset_vector();
        {
            static int idle_ready;
            if (!idle_ready) {
                idle_loops_init();
                idle_ready = 1;
            }
        }
        frame_end = g_cpu.master_cycles + cycles_to_next_vblank();
        game_run_slices(frame_end);
        return;
    }

    /* Vblank edge: the beam is at scanline 225. NMITIMEN gates the NMI. */
    frame_end = g_cpu.master_cycles + cycles_to_next_vblank();
    if (rtl_trace_level() >= 3)
        fprintf(stderr, "[asp_rtl] f%d frame start beam=%u,%u nmi=%d\n",
                snes_frame_counter, (unsigned)g_snes->vPos,
                (unsigned)g_snes->hPos, (int)g_snes->nmiEnabled);
    if (g_snes->nmiEnabled) {
        g_snes->inNmi = true;
        game_run_interrupt("NMI", nmi_vector(), frame_end);
        g_snes->inNmi = false;
    }
    charge_hdma_debt(frame_end);
    game_run_slices(frame_end);
}

/* Presentation only. The field is rasterised from the PPU state the guest
 * left at vblank, with HDMA re-run per line. It must not execute guest code:
 * the V-IRQ was already latched by the beam and serviced in the CPU slices
 * (game_run_slices). Running the handler again here — as the scaffold's
 * generic driver did at line == VTIME — executes it twice per frame, and on
 * A.S.P. that handler is the task scheduler (a second context switch per
 * frame; the game programs VTIME 9, a visible line, in some scenes). */
void GameDrawPpuFrame(void)
{
    SimpleHdma hdma_chans[8];
    Dma *dma = g_snes->dma;
    int line, ch;

    /* Re-arm HDMA from the last $420C (HDMAEN) the guest wrote — typically
     * during the NMI just run. The framework records it for exactly this. */
    dma_startDma(dma, g_snesrecomp_last_hdmaen, true);
    for (ch = 0; ch < 8; ch++)
        SimpleHdma_Init(&hdma_chans[ch], &dma->channel[ch]);

    /* From line 0, not line 1: starting at 1 leaves the top scanline holding
     * the previous frame's state, which shows up as a stripe of stale
     * tilemap above a HUD. HDMA runs in the H-blank before each line. */
    for (line = 0; line <= 224; line++) {
        uint32_t line_cost = 0;
        for (ch = 0; ch < 8; ch++) {
            SimpleHdma *c = &hdma_chans[ch];
            if (c->table != NULL) {
                const int reload = (c->rep_count & 0x7f) == 0;
                const int repeat = (c->rep_count & 0x80) != 0;
                static const uint8_t kLen[8] = {1, 2, 2, 4, 4, 4, 2, 4};
                line_cost += 8;
                if (reload)
                    line_cost += 8 + ((c->mode & 0x40) ? 16 : 0);
                SimpleHdma_DoLine(c);
                if ((reload && c->table != NULL) || repeat)
                    line_cost += 8u * kLen[c->mode & 7];
            }
        }
        if (line_cost)
            line_cost += 18;
        if (hdma_steal_enabled())
            g_hdma_debt += line_cost;
        ppu_runLine(g_ppu, line);
    }
}

const RtlGameInfo kGameInfo = {
    .title = "aspairstrikepatrolita",
    .initialize = NULL,
    .run_frame = &GameRunOneFrame,
    .draw_ppu_frame = &GameDrawPpuFrame,
    .save_name_prefix = "save",
};

void GameSessionReset(void)
{
    /* Rematch / soft-return: clear anything that must not survive a new
     * session. recomp-ai-rules/NETPLAY.md §3 — sticky state that "has always
     * been fine" is the usual desync culprit, because single-player never
     * re-enters the boot path twice in one process. */
    g_resume_pc = 0;
    g_hdma_debt = 0;
}
