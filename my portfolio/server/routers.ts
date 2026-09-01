import { COOKIE_NAME } from "@shared/const";
import { getSessionCookieOptions } from "./_core/cookies";
import { systemRouter } from "./_core/systemRouter";
import { publicProcedure, router } from "./_core/trpc";
import { getTikTokStats, formatFollowerCount } from "./tiktok";

export const appRouter = router({
    // if you need to use socket.io, read and register route in server/_core/index.ts, all api should start with '/api/' so that the gateway can route correctly
  system: systemRouter,
  auth: router({
    me: publicProcedure.query(opts => opts.ctx.user),
    logout: publicProcedure.mutation(({ ctx }) => {
      const cookieOptions = getSessionCookieOptions(ctx.req);
      ctx.res.clearCookie(COOKIE_NAME, { ...cookieOptions, maxAge: -1 });
      return {
        success: true,
      } as const;
    }),
  }),

  tiktok: router({
    stats: publicProcedure.query(async () => {
      const stats = await getTikTokStats();
      if (!stats) {
        return {
          followers: 3637,
          likes: 24500,
          formatted: { followers: '3.6K', likes: '24.5K' },
          isLive: false,
        };
      }
      return {
        followers: stats.followers,
        likes: stats.likes,
        formatted: {
          followers: formatFollowerCount(stats.followers),
          likes: formatFollowerCount(stats.likes),
        },
        isLive: true,
      };
    }),
  }),
});

export type AppRouter = typeof appRouter;
