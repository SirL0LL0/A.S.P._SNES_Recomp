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
 * (default: logical cores - 1, at most 4); 1 renders on the caller only.
 */
#include "asp_render_mt.h"

#include <stdlib.h>

#include <SDL3/SDL.h>

#include "snes/ppu.h"

#define ASP_MT_MAX 8
#define ASP_MT_SPIN 20000   /* pause iterations before a worker sleeps */

static struct {
    int n;                          /* threads including the caller */
    SDL_AtomicInt gen;              /* bumped once per job */
    SDL_AtomicInt done;             /* workers finished with the job */
    SDL_AtomicInt sleepers;
    SDL_Semaphore *wake;
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
            SDL_AddAtomicInt(&g_mt.sleepers, 1);
            if (SDL_GetAtomicInt(&g_mt.gen) == seen)
                SDL_WaitSemaphore(g_mt.wake);
            SDL_AddAtomicInt(&g_mt.sleepers, -1);
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
    int b, e, k, sleepers;
    g_mt.fn = fn;
    g_mt.ctx = ctx;
    g_mt.begin = begin;
    g_mt.end = end;
    SDL_SetAtomicInt(&g_mt.done, 0);
    SDL_AddAtomicInt(&g_mt.gen, 1);         /* publishes the job */
    sleepers = SDL_GetAtomicInt(&g_mt.sleepers);
    for (k = 0; k < sleepers; k++)
        SDL_SignalSemaphore(g_mt.wake);
    chunk(0, &b, &e);
    if (b < e)
        fn(ctx, b, e);
    while (SDL_GetAtomicInt(&g_mt.done) != g_mt.n - 1)
        SDL_CPUPauseInstruction();
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
        n = SDL_GetNumLogicalCPUCores() - 1;
        if (n > 4)
            n = 4;
    }
    if (n > ASP_MT_MAX)
        n = ASP_MT_MAX;
    if (n < 2)
        return;                             /* single thread: no executor */
    g_mt.wake = SDL_CreateSemaphore(0);
    if (!g_mt.wake)
        return;
    g_mt.n = n;
    for (i = 1; i < n; i++) {
        SDL_Thread *t = SDL_CreateThread(worker, "asp_ppu", (void *)(intptr_t)i);
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
