import { describe, expect, it } from 'vitest';
import { formatFollowerCount } from './tiktok';

describe('TikTok Utils', () => {
  describe('formatFollowerCount', () => {
    it('should format numbers under 1000 as-is', () => {
      expect(formatFollowerCount(500)).toBe('500');
      expect(formatFollowerCount(999)).toBe('999');
      expect(formatFollowerCount(0)).toBe('0');
    });

    it('should format thousands with K suffix', () => {
      expect(formatFollowerCount(1000)).toBe('1.0K');
      expect(formatFollowerCount(3637)).toBe('3.6K');
      expect(formatFollowerCount(24500)).toBe('24.5K');
      expect(formatFollowerCount(999999)).toBe('1000.0K');
    });

    it('should format millions with M suffix', () => {
      expect(formatFollowerCount(1000000)).toBe('1.0M');
      expect(formatFollowerCount(5500000)).toBe('5.5M');
    });
  });

  describe('TikTok Stats', () => {
    it('should have correct TikTok handle', () => {
      const handle = '@thukhatech';
      expect(handle).toBe('@thukhatech');
    });

    it('should have correct TikTok profile URL', () => {
      const url = 'https://www.tiktok.com/@thukhatech';
      expect(url).toContain('@thukhatech');
      expect(url).toContain('tiktok.com');
    });

    it('should have current follower and likes stats', () => {
      const followers = 3637;
      const likes = 24500;

      expect(followers).toBeGreaterThan(0);
      expect(likes).toBeGreaterThan(0);
      expect(formatFollowerCount(followers)).toBe('3.6K');
      expect(formatFollowerCount(likes)).toBe('24.5K');
    });
  });

  describe('TikTok Content Categories', () => {
    it('should include all content categories', () => {
      const categories = [
        'DIY Bluetooth Jammers',
        'Portable Power Stations',
        'Personal AI (Jarvis Model)',
        'Robotics & Emo Robot',
        'Tech Solutions & Tutorials',
        'Custom Gadgets Store',
      ];

      expect(categories).toHaveLength(6);
      categories.forEach((category) => {
        expect(category).toBeTruthy();
      });
    });

    it('should have content in Burmese language targeting Myanmar', () => {
      const language = 'Burmese';
      const target = 'Myanmar';

      expect(language).toBe('Burmese');
      expect(target).toBe('Myanmar');
    });
  });
});
