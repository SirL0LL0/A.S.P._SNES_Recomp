/*
 * A.S.P. trucchi — RAM cheats as trusted mod plugins.
 *
 * Only Pro Action Replay codes, i.e. plain WRAM writes. Game Genie codes
 * patch the ROM and are deliberately not offered: they would change the
 * program the recomp was generated from.
 *
 * A PAR device rewrites its values every frame, whatever the game is doing.
 * A.S.P. reuses this WRAM in other screens, so the writes here are gated on
 * the game mode at $0200 (0 menu/setup, 2 HQ/briefing, 1 flight), measured
 * on the flight scenario: $0806/$080A/$081C/$081E hold armour, fuel, vulcan
 * and missiles only while flying, and $00A0 becomes an unrelated $10 at HQ.
 *
 * The package is mods/preloaded/packages/asp.trucchi; each feature there
 * selects one plugin id registered below. The writes happen inside the
 * guest frame (start of GameRunOneFrame, as a PAR would at vblank), so they
 * are part of the simulation: deterministic for replays, rewind, netplay.
 *
 * ASP_CHEATS=fuel,missiles,armor,vulcan,mania,cockpit_c (or "all") enables
 * them without the launcher, for headless tests.
 */
#include "asp_cheats.h"

#include <stdlib.h>
#include <string.h>

#include "common_rtl.h"         /* g_ram */
#include "mod_runtime.h"

enum {
    kModeMenu = 0,
    kModeFlight = 1,
    kModeHq = 2,
};

typedef struct {
    const char *name;      /* ASP_CHEATS token, plugin id suffix */
    int mode;              /* $0200 value the writes are limited to */
    uint8_t n;
    struct { uint16_t addr; uint8_t value; } w[2];
} AspCheat;

static const AspCheat kCheats[] = {
    /* 7E080A:C0 7E080B:A8 — the full tank the game loads at take-off. */
    {"fuel",      kModeFlight, 2, {{0x080A, 0xC0}, {0x080B, 0xA8}}},
    /* 7E081E:63 — 99 missiles. */
    {"missiles",  kModeFlight, 1, {{0x081E, 0x63}}},
    /* 7E0806:20 — armour kept full ("Infinite Damage"). */
    {"armor",     kModeFlight, 1, {{0x0806, 0x20}}},
    /* 7E081C:00 7E081D:02 — vulcan ammunition. */
    {"vulcan",    kModeFlight, 2, {{0x081C, 0x00}, {0x081D, 0x02}}},
    /* 7E00A0:04 — unused "Mania" difficulty on the setup screen. */
    {"mania",     kModeMenu,   1, {{0x00A0, 0x04}}},
    /* 7E00A2:04 — unused "Cockpit C" control scheme on the setup screen. */
    {"cockpit_c", kModeMenu,   1, {{0x00A2, 0x04}}},
};
#define ASP_CHEAT_COUNT ((int)(sizeof(kCheats) / sizeof(kCheats[0])))

static uint32_t g_cheat_mask;      /* from the mod runtime */
static uint32_t g_cheat_env_mask;  /* from ASP_CHEATS */

static void env_init(void)
{
    static int done;
    const char *e;
    int i;
    if (done)
        return;
    done = 1;
    e = getenv("ASP_CHEATS");
    if (!e || !*e)
        return;
    for (i = 0; i < ASP_CHEAT_COUNT; i++)
        if (!strcmp(e, "all") || strstr(e, kCheats[i].name))
            g_cheat_env_mask |= 1u << i;
}

void asp_cheats_apply(void)
{
    uint32_t mask;
    uint8_t mode;
    int i, k;
    env_init();
    mask = g_cheat_mask | g_cheat_env_mask;
    if (!mask)
        return;
    mode = g_ram[0x0200];
    for (i = 0; i < ASP_CHEAT_COUNT; i++) {
        const AspCheat *c = &kCheats[i];
        if (!(mask & (1u << i)) || mode != c->mode)
            continue;
        for (k = 0; k < c->n; k++)
            g_ram[c->w[k].addr] = c->w[k].value;
    }
}

/* One activation callback per cheat: the runtime calls the callbacks of the
 * enabled features, after the reset callback cleared them all. */
#define ASP_CHEAT_PLUGIN(idx, sym) \
    static void sym(void) { g_cheat_mask |= 1u << (idx); }
ASP_CHEAT_PLUGIN(0, enable_fuel)
ASP_CHEAT_PLUGIN(1, enable_missiles)
ASP_CHEAT_PLUGIN(2, enable_armor)
ASP_CHEAT_PLUGIN(3, enable_vulcan)
ASP_CHEAT_PLUGIN(4, enable_mania)
ASP_CHEAT_PLUGIN(5, enable_cockpit_c)

static void reset_cheats(void) { g_cheat_mask = 0; }

SNES_MOD_CONSTRUCTOR(asp_register_cheats) {
    snes_mod_register_reset_callback(reset_cheats);
    snes_mod_register_activation_plugin("asp.trucchi.fuel", enable_fuel);
    snes_mod_register_activation_plugin("asp.trucchi.missiles", enable_missiles);
    snes_mod_register_activation_plugin("asp.trucchi.armor", enable_armor);
    snes_mod_register_activation_plugin("asp.trucchi.vulcan", enable_vulcan);
    snes_mod_register_activation_plugin("asp.trucchi.mania", enable_mania);
    snes_mod_register_activation_plugin("asp.trucchi.cockpit_c", enable_cockpit_c);
}
