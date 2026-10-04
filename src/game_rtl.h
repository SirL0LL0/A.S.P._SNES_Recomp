#ifndef GAME_RTL_H
#define GAME_RTL_H

#include "common_cpu_infra.h"

/* One frame of A.S.P. Air Strike Patrol ITA. See game_rtl.c — this is the port. */
void GameRunOneFrame(void);
void GameDrawPpuFrame(void);
void GameSessionReset(void);

extern const RtlGameInfo kGameInfo;

/* Full-resolution picture: 512x448 ARGB, one pixel per hi-res dot and per
 * interlaced line (see asp_hd_enable in game_rtl.c). */
enum { kAspHdWidth = 512, kAspHdHeight = 448 };
void asp_hd_enable(int on);
const uint32_t *asp_hd_frame(void);

#endif /* GAME_RTL_H */
