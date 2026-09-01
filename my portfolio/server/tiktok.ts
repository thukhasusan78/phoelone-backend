import axios from 'axios';

interface TikTokStats {
  followers: number;
  likes: number;
  timestamp: number;
}

// In-memory cache with 1-hour TTL
let cachedStats: TikTokStats | null = null;
const CACHE_DURATION = 60 * 60 * 1000; // 1 hour in milliseconds

/**
 * Scrape TikTok profile to get follower count
 * Uses a simple approach to extract stats from the profile page
 */
async function scrapeTikTokStats(): Promise<TikTokStats | null> {
  try {
    // Check cache first
    if (cachedStats && Date.now() - cachedStats.timestamp < CACHE_DURATION) {
      console.log('[TikTok] Using cached stats');
      return cachedStats;
    }

    console.log('[TikTok] Fetching fresh stats from profile...');

    // Fetch the TikTok profile page with a browser-like user agent
    const response = await axios.get('https://www.tiktok.com/@thukhatech', {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
      },
      timeout: 10000,
    });

    const html = response.data;

    // Extract stats from the HTML using regex patterns
    // TikTok embeds stats in JSON-LD or meta tags
    
    // Try to find follower count in various formats
    let followers = 0;
    let likes = 0;

    // Pattern 1: Look for follower count in common TikTok HTML patterns
    const followerMatch = html.match(/followerCount["\']?\s*:\s*(\d+)/i) ||
                          html.match(/["\']followerCount["\']?\s*:\s*["\']?(\d+)/i) ||
                          html.match(/followers["\']?\s*:\s*["\']?(\d+)/i);
    
    if (followerMatch) {
      followers = parseInt(followerMatch[1], 10);
    }

    // Pattern 2: Look for likes/hearts count
    const likesMatch = html.match(/heartCount["\']?\s*:\s*(\d+)/i) ||
                       html.match(/["\']heartCount["\']?\s*:\s*["\']?(\d+)/i) ||
                       html.match(/likes["\']?\s*:\s*["\']?(\d+)/i);
    
    if (likesMatch) {
      likes = parseInt(likesMatch[1], 10);
    }

    // If we couldn't extract from HTML, return null
    if (followers === 0 && likes === 0) {
      console.warn('[TikTok] Could not extract stats from HTML');
      return null;
    }

    const stats: TikTokStats = {
      followers,
      likes,
      timestamp: Date.now(),
    };

    // Update cache
    cachedStats = stats;
    console.log(`[TikTok] Updated stats: ${followers} followers, ${likes} likes`);

    return stats;
  } catch (error) {
    console.error('[TikTok] Error scraping profile:', error instanceof Error ? error.message : error);
    
    // Return cached stats if available, even if expired
    if (cachedStats) {
      console.log('[TikTok] Returning stale cached stats');
      return cachedStats;
    }
    
    return null;
  }
}

/**
 * Format follower count for display
 */
export function formatFollowerCount(count: number): string {
  if (count >= 1000000) {
    return `${(count / 1000000).toFixed(1)}M`;
  }
  if (count >= 1000) {
    return `${(count / 1000).toFixed(1)}K`;
  }
  return count.toString();
}

/**
 * Get TikTok stats (with caching)
 */
export async function getTikTokStats(): Promise<TikTokStats | null> {
  return scrapeTikTokStats();
}

/**
 * Clear the cache (useful for testing or manual refresh)
 */
export function clearTikTokCache(): void {
  cachedStats = null;
  console.log('[TikTok] Cache cleared');
}
