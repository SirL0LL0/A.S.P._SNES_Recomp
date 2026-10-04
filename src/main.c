/*
 * A.S.P. Air Strike Patrol ITA — desktop host shim.
 *
 * Identity and hooks, nothing else. The host itself is the framework's
 * (snesrecomp/runner/src/desktop/host_main.h): the pre-boot launcher, ROM
 * resolution and digest checks, config.ini and keybinds.ini, the window and
 * the SDL / OpenGL presenters, audio, gamepads, the in-game save-state
 * browser and rewind filmstrip, the OSD, the pacing clock, crash handlers and
 * the post-mortem report, mod packages, Generate & rebuild, and the netplay
 * barrier when the project is built with it. A fix there reaches this
 * project on a submodule pull; nothing in this file needs to change for it.
 *
 * This file used to BE the host -- a ~700-line copy made at scaffold time --
 * so every project scaffolded before a fix kept the bug. Two ports of the
 * same game, a week apart, had different hosts.
 *
 * Nothing here ever ships a ROM. The player supplies one; the host makes
 * that easy (launcher, then positional argument, then a copy beside the
 * executable, then the rom.cfg cache, then a file picker) and checks what it
 * is handed against the digests in rom_identity.txt.
 *
 * Grow this file only with what is specific to THIS title: a custom
 * presenter (prepare_frame / draw_frame), an SPC player, a pacing rule
 * (keep_pacing_debt), a Mods provider. See SnesDesktopHostGame for the
 * complete list of hooks and what each is for.
 */

#include "host_main.h"
#include "game_rtl.h"
#include "snesrecomp_rom_identity.h"  /* generated from rom_identity.txt */

#ifndef __ANDROID__
#define SDL_MAIN_HANDLED 1
#endif
#include "desktop/sdl_compat.h"
#ifdef __ANDROID__
/* SDLActivity loads libmain.so and dlsyms its SDL_main. */
#define main SDL_main
#endif

#ifndef SNES_GAME_VERSION
#define SNES_GAME_VERSION "dev"
#endif

#include "desktop/config.h"   /* g_config */

#include <stdlib.h>
#include <string.h>

/* A.S.P. needs the per-pixel REFERENCE PPU renderer. The span renderer
 * (config NewRenderer = 1, the scaffold default) drops the mission intro
 * ("OPERAZIONE DESERT CORRADO / DISTRUGGI SITI RADAR") and the briefing text
 * under the mission map: measured on the same build and route, the reference
 * renderer shows both, the span renderer leaves them blank. Force it here,
 * after config.ini is parsed and before the host derives its render flags,
 * so an existing config.ini cannot switch it back. (The ToggleRenderer hotkey,
 * `r` by default, still flips it at runtime: press it again to restore.) */
static void asp_after_config(void)
{
    g_config.new_renderer = false;
}

/* Present the renderer's 512x448 picture (see asp_hd_enable). */
static int asp_draw_frame(uint8_t *dst, size_t pitch, const uint8_t *field,
                          int frame_w, int frame_h, double alpha)
{
    const uint32_t *src = asp_hd_frame();
    int y;
    (void)field; (void)frame_w; (void)frame_h; (void)alpha;
    for (y = 0; y < kAspHdHeight; y++)
        memcpy(dst + (size_t)y * pitch, src + (size_t)y * kAspHdWidth,
               (size_t)kAspHdWidth * 4u);
    return 1;
}

static SnesDesktopHostGame kGameHost = {
    .display_name        = "A.S.P. Air Strike Patrol ITA",
    .window_title        = "A.S.P. Air Strike Patrol ITA",
    .region              = SNESRECOMP_ROM_REGION,
    .rom_file            = SNESRECOMP_ROM_FILE,
    .expected_sha256_hex = SNESRECOMP_ROM_EXPECTED_SHA256,
    .expected_crc32_hex  = SNESRECOMP_ROM_EXPECTED_CRC32,
    .game_id             = SNESRECOMP_ROM_GAME_ID,
    .build_version       = SNES_GAME_VERSION,
    .game_info           = &kGameInfo,
    .num_players         = 1,
    /* Battery-backed SRAM shows the launcher's SAVES panel. Leave NULL for a
     * title without one. The path is exe-relative. */
    .sram_path           = "saves/save.srm",
    .after_config        = asp_after_config,
    .draw_frame          = asp_draw_frame,
    .present_scale       = 2,
};

#ifndef __ANDROID__
#undef main   /* desktop: keep plain main() even if SDL_main.h remapped it */
#endif
int main(int argc, char **argv)
{
    /* ASP_HD=0: present the plain 256x224 field (main-screen dots, field 0). */
    const char *hd = getenv("ASP_HD");
    if (hd && *hd == '0') {
        kGameHost.draw_frame = NULL;
        kGameHost.present_scale = 0;
    } else {
        asp_hd_enable(1);
    }
    return snesrecomp_desktop_main(&kGameHost, argc, argv);
}
