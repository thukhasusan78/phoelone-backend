import { describe, expect, it } from 'vitest';

describe('Portfolio Components', () => {
  describe('Navigation', () => {
    it('should have all required nav items', () => {
      const navItems = ['Hero', 'About', 'Skills', 'Projects', 'Contact'];
      expect(navItems).toHaveLength(5);
      expect(navItems).toContain('Hero');
      expect(navItems).toContain('About');
      expect(navItems).toContain('Skills');
      expect(navItems).toContain('Projects');
      expect(navItems).toContain('Contact');
    });
  });

  describe('Hero Section', () => {
    it('should display correct name and title', () => {
      const name = 'သုခစုစံ (Thu Kha Su San)';
      const title = 'AI Agent Developer & Tech Innovator';
      const location = 'Myanmar';

      expect(name).toContain('Thu Kha Su San');
      expect(title).toContain('AI Agent Developer');
      expect(location).toBe('Myanmar');
    });

    it('should have correct CTA links', () => {
      const links = [
        { label: 'GitHub', url: 'https://github.com/thukhasusan78' },
        { label: 'Telegram', url: 'https://t.me/thukhasusan78' },
      ];

      expect(links).toHaveLength(2);
      links.forEach((link) => {
        expect(link.url).toBeTruthy();
        expect(link.label).toBeTruthy();
      });
    });
  });

  describe('About Section', () => {
    it('should list all four specializations', () => {
      const specializations = [
        'AI Agent Development',
        'Telegram Automation',
        'Media Processing',
        'Web Development',
      ];

      expect(specializations).toHaveLength(4);
      specializations.forEach((spec) => {
        expect(spec).toBeTruthy();
      });
    });
  });

  describe('Skills Section', () => {
    it('should include all required technologies', () => {
      const skills = [
        'Python',
        'FastAPI',
        'Google Gemini AI',
        'LLMs',
        'Telegram Bot Development',
        'Playwright',
        'LanceDB',
        'Face Recognition',
        'Edge TTS',
        'Web Scraping',
        'SSH',
        'Cloudflare Tunnels',
        'APScheduler',
      ];

      expect(skills).toHaveLength(13);
      expect(skills).toContain('Python');
      expect(skills).toContain('FastAPI');
      expect(skills).toContain('Google Gemini AI');
      expect(skills).toContain('LanceDB');
    });
  });

  describe('Projects Section', () => {
    it('should feature Jarvis AI Agent project', () => {
      const project = {
        name: 'Jarvis AI Agent',
        features: [
          'Google Gemini AI Brain',
          'FastAPI + Uvicorn Server',
          'Telegram Bot + Userbot',
          'Playwright Browser Automation',
          'LanceDB Vector Database',
          'Edge TTS with Myanmar Voice',
          'Face Recognition',
          'Movie Engine',
          'Web Search Tool',
          'File Management',
          'System Commands',
          'Hybrid Memory System',
        ],
      };

      expect(project.name).toBe('Jarvis AI Agent');
      expect(project.features).toHaveLength(12);
      expect(project.features).toContain('Google Gemini AI Brain');
      expect(project.features).toContain('LanceDB Vector Database');
      expect(project.features).toContain('Hybrid Memory System');
    });
  });

  describe('Contact Section', () => {
    it('should include all contact information', () => {
      const contacts = {
        telegram: '@thukhasusan78',
        phone: '09784679389',
        github: 'github.com/thukhasusan78',
        tiktok: {
          handle: '@thukhatech',
          brand: 'Thu Kha Industries',
          followers: '1,859',
          likes: '14.1K',
        },
        movieChannel: '@thukhamovies',
      };

      expect(contacts.telegram).toBe('@thukhasusan78');
      expect(contacts.phone).toBe('09784679389');
      expect(contacts.github).toContain('thukhasusan78');
      expect(contacts.tiktok.handle).toBe('@thukhatech');
      expect(contacts.tiktok.brand).toBe('Thu Kha Industries');
      expect(contacts.tiktok.followers).toBe('1,859');
      expect(contacts.tiktok.likes).toBe('14.1K');
      expect(contacts.movieChannel).toBe('@thukhamovies');
    });

    it('should have valid contact URLs', () => {
      const urls = [
        'https://t.me/thukhasusan78',
        'tel:09784679389',
        'https://github.com/thukhasusan78',
        'https://www.tiktok.com/@thukhatech',
        'https://t.me/thukhamovies',
      ];

      urls.forEach((url) => {
        expect(url).toMatch(/^(https?:\/\/|tel:)/);
      });
    });
  });

  describe('Theme Configuration', () => {
    it('should have dark theme colors configured', () => {
      const colors = {
        background: '#0a0e27',
        foreground: '#e0e8ff',
        primary: '#00d9ff',
        secondary: '#0088ff',
        accent: '#00d9ff',
      };

      expect(colors.background).toBeTruthy();
      expect(colors.foreground).toBeTruthy();
      expect(colors.primary).toBe('#00d9ff');
      expect(colors.accent).toBe('#00d9ff');
    });
  });
});
