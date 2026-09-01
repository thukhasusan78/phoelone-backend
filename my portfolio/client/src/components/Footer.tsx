import { Heart } from 'lucide-react';

export default function Footer() {
  const currentYear = new Date().getFullYear();

  return (
    <footer className="border-t border-border bg-background/50 backdrop-blur-sm relative z-10">
      <div className="container mx-auto px-4 py-8">
        <div className="max-w-4xl mx-auto">
          <div className="flex flex-col md:flex-row items-center justify-between gap-4">
            {/* Left side - branding */}
            <div className="text-center md:text-left">
              <p className="text-foreground/70 flex items-center justify-center md:justify-start gap-2">
                Made with <Heart size={16} className="text-primary" /> by Thu Kha Su San
              </p>
            </div>

            {/* Center - year */}
            <div className="text-foreground/60 text-sm">
              © {currentYear} All rights reserved
            </div>

            {/* Right side - links */}
            <div className="flex gap-6 text-sm">
              <a
                href="https://github.com/thukhasusan78"
                target="_blank"
                rel="noopener noreferrer"
                className="text-foreground/70 hover:text-primary transition-colors duration-300"
              >
                GitHub
              </a>
              <a
                href="https://t.me/thukhasusan78"
                target="_blank"
                rel="noopener noreferrer"
                className="text-foreground/70 hover:text-primary transition-colors duration-300"
              >
                Telegram
              </a>
              <a
                href="https://www.tiktok.com/@thukhatech"
                target="_blank"
                rel="noopener noreferrer"
                className="text-foreground/70 hover:text-primary transition-colors duration-300"
              >
                TikTok
              </a>
            </div>
          </div>
        </div>
      </div>
    </footer>
  );
}
