/*
 * Multi-threaded scanline rendering for the reference PPU renderer.
 *
 * A.S.P. needs the per-dot reference renderer (the span renderer drops its
 * briefing text), and its menus and HQ screens are hi-res + interlaced, so
 * with the 512x448 output every dot is resolved four times (main and sub
 * screen, both fields). Measured on the HQ menu: ~20 ms of a 16.7 ms field
 * on one core, i.e. the game ran at ~48 fps and every menu reacted late.
 *
 * Each line is split into equal dot ranges, one per thread; the renderer
 * guarantees the per-dot work only reads the PPU and writes its own dots
 * (ppu_legacy_set_span_executor), so the picture is identical for any
 * number of threads. Workers spin briefly between lines (lines arrive every
 * few tens of microseconds while the beam runs) and sleep on a semaphore
 * when idle (vblank, presentation), so an idle game does not burn cores.
 *
 * ASP_RENDER_THREADS=n sets the thread count including the caller
 * (default: half the logical cores, at most 4); 1 renders on the caller only.
 */
#include "asp_render_mt.h"

#include <stdlib.h>

#include <SDL3/SDL.h>

#include "snes/ppu.h"

#define ASP_MT_MAX 8
#define ASP_MT_SPIN 4000    /* pause iterations before a worker sleeps */
#define ASP_MT_YIELD 2000   /* caller: pause iterations before yielding */

static struct {
    int n;                          /* threads including the caller */
    SDL_AtomicInt gen;              /* bumped once per job */
    SDL_AtomicInt done;             /* workers finished with the job */
    /* One semaphore and sleeping flag PER worker. A shared semaphore lost
     * wake-ups: a fast worker could finish its chunk, go back to sleep and
     * consume the signal meant for a slower one, which then never ran its
     * chunk while the caller waited for it forever (black screen at boot). */
    SDL_AtomicInt sleeping[ASP_MT_MAX];
    SDL_Semaphore *wake[ASP_MT_MAX];
    PpuLegacySpanFn *fn;
    void *ctx;
    int begin, end;
} g_mt;

static void chunk(int i, int *b, int *e)
{
    const int len = g_mt.end - g_mt.begin;
    *b = g_mt.begin + len * i / g_mt.n;
    *e = g_mt.begin + len * (i + 1) / g_mt.n;
}

static int SDLCALL worker(void *data)
{
    const int index = (int)(intptr_t)data;
    int seen = 0;
    for (;;) {
        int spins = 0, gen;
        while ((gen = SDL_GetAtomicInt(&g_mt.gen)) == seen) {
            if (++spins < ASP_MT_SPIN) {
                SDL_CPUPauseInstruction();
                continue;
            }
            SDL_SetAtomicInt(&g_mt.sleeping[index], 1);
            if (SDL_GetAtomicInt(&g_mt.gen) == seen)
                SDL_WaitSemaphore(g_mt.wake[index]);
            SDL_SetAtomicInt(&g_mt.sleeping[index], 0);
            spins = 0;
        }
        seen = gen;
        {
            int b, e;
            chunk(index, &b, &e);
            if (b < e)
                g_mt.fn(g_mt.ctx, b, e);
        }
        SDL_AddAtomicInt(&g_mt.done, 1);
    }
    return 0;
}

static void execute(PpuLegacySpanFn *fn, void *ctx, int begin, int end)
{
    int b, e, k;
    g_mt.fn = fn;
    g_mt.ctx = ctx;
    g_mt.begin = begin;
    g_mt.end = end;
    SDL_SetAtomicInt(&g_mt.done, 0);
    SDL_AddAtomicInt(&g_mt.gen, 1);         /* publishes the job */
    /* Set-then-check on both sides: a worker that went to sleep before the
     * bump is seen here, one that checks after it sees the new job. A stale
     * extra signal only wakes that same worker once for nothing. */
    for (k = 1; k < g_mt.n; k++)
        if (SDL_GetAtomicInt(&g_mt.sleeping[k]))
            SDL_SignalSemaphore(g_mt.wake[k]);
    chunk(0, &b, &e);
    if (b < e)
        fn(ctx, b, e);
    for (k = 0; SDL_GetAtomicInt(&g_mt.done) != g_mt.n - 1; k++) {
        /* Never spin unboundedly: with more threads than free cores the
         * worker we wait for may need this core to run. */
        if (k < ASP_MT_YIELD)
            SDL_CPUPauseInstruction();
        else
            SDL_DelayNS(0);
    }
}

void asp_render_mt_init(void)
{
    static int started;
    const char *env = getenv("ASP_RENDER_THREADS");
    int n, i;
    if (started)
        return;
    started = 1;
    if (env && *env) {
        n = atoi(env);
    } else {
        /* Half the logical cores (SMT siblings share an execution core, and
         * the emulation, audio and presenter threads need theirs), max 4. */
        n = SDL_GetNumLogicalCPUCores() / 2;
        if (n > 4)
            n = 4;
    }
    if (n > ASP_MT_MAX)
        n = ASP_MT_MAX;
    if (n < 2)
        return;                             /* single thread: no executor */
    g_mt.n = n;
    for (i = 1; i < n; i++) {
        SDL_Thread *t;
        g_mt.wake[i] = SDL_CreateSemaphore(0);
        t = g_mt.wake[i] ? SDL_CreateThread(worker, "asp_ppu", (void *)(intptr_t)i) : NULL;
        if (!t) {
            /* Run with the workers that did start. */
            g_mt.n = i;
            break;
        }
        SDL_DetachThread(t);
    }
    if (g_mt.n >= 2)
        ppu_legacy_set_span_executor(execute);
}
