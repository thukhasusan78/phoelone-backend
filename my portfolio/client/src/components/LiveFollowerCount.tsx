import { useEffect, useState } from 'react';
import { trpc } from '@/lib/trpc';
import { Zap } from 'lucide-react';

interface FollowerStats {
  followers: number;
  likes: number;
  formatted: {
    followers: string;
    likes: string;
  };
  isLive: boolean;
}

export default function LiveFollowerCount() {
  const [stats, setStats] = useState<FollowerStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Fetch TikTok stats from server
  const { data: tiktokStats, isLoading: isFetching } = trpc.tiktok.stats.useQuery();

  useEffect(() => {
    if (tiktokStats) {
      setStats(tiktokStats);
      setIsLoading(false);
    }
  }, [tiktokStats]);

  if (isLoading || isFetching) {
    return (
      <div className="flex items-center gap-2 text-primary">
        <div className="w-2 h-2 rounded-full bg-primary animate-pulse" />
        <span className="text-sm">Loading stats...</span>
      </div>
    );
  }

  if (!stats) {
    return null;
  }

  return (
    <div className="flex items-center gap-3 px-4 py-2 rounded-lg border border-primary/30 bg-primary/5 backdrop-blur-sm">
      <div className="flex items-center gap-2">
        <Zap size={16} className="text-primary" />
        <span className="text-xs font-semibold text-primary uppercase tracking-wider">
          {stats.isLive ? 'Live' : 'Cached'}
        </span>
      </div>
      <div className="h-4 w-px bg-primary/30" />
      <div className="flex items-center gap-4">
        <div className="text-center">
          <div className="text-sm font-bold text-primary">
            {stats.formatted.followers}
          </div>
          <div className="text-xs text-foreground/60">Followers</div>
        </div>
        <div className="text-center">
          <div className="text-sm font-bold text-primary">
            {stats.formatted.likes}
          </div>
          <div className="text-xs text-foreground/60">Likes</div>
        </div>
      </div>
    </div>
  );
}
