import { useEffect, useState } from 'react';
import { ArrowRight, Github, Send } from 'lucide-react';
import LiveFollowerCount from './LiveFollowerCount';

export default function Hero() {
  const [displayText, setDisplayText] = useState('');
  const fullText = 'Thu Kha Su San';
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    setIsVisible(true);
    let index = 0;
    const interval = setInterval(() => {
      if (index < fullText.length) {
        setDisplayText(fullText.slice(0, index + 1));
        index++;
      } else {
        clearInterval(interval);
      }
    }, 50);

    return () => clearInterval(interval);
  }, []);

  return (
    <section
      id="hero"
      className="min-h-screen flex items-center justify-center pt-20 relative overflow-hidden"
    >
      <div className="container mx-auto px-4 z-10">
        <div className="max-w-4xl mx-auto text-center">
          {/* Animated name */}
          <div className={`mb-6 transition-all duration-1000 ${isVisible ? 'opacity-100' : 'opacity-0'}`}>
            <h1 className="text-5xl md:text-7xl font-bold mb-4 glow-text">
              {displayText}
              {displayText.length < fullText.length && <span className="animate-pulse">_</span>}
            </h1>
          </div>

          {/* Title and location */}
          <div className={`mb-8 transition-all duration-1000 delay-300 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-10'}`}>
            <p className="text-xl md:text-2xl text-primary mb-2">
              AI Agent Developer & Tech Innovator
            </p>
            <p className="text-lg text-muted-foreground">
              Based in Myanmar 🇲🇲
            </p>
          </div>

          {/* Description */}
          <div className={`mb-12 transition-all duration-1000 delay-500 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-10'}`}>
            <p className="text-base md:text-lg text-foreground/80 max-w-2xl mx-auto leading-relaxed">
              Specializing in AI Agent Development, Telegram Automation, Media Processing, and Web Development.
              Building intelligent systems that push the boundaries of what's possible.
            </p>
          </div>

          {/* Live TikTok Stats */}
          <div className={`mb-12 flex justify-center transition-all duration-1000 delay-600 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-10'}`}>
            <LiveFollowerCount />
          </div>

          {/* CTA Buttons */}
          <div className={`flex flex-col sm:flex-row gap-4 justify-center transition-all duration-1000 delay-700 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-10'}`}>
            <a
              href="https://github.com/thukhasusan78"
              target="_blank"
              rel="noopener noreferrer"
              className="neon-button flex items-center justify-center gap-2 group"
            >
              <Github size={20} />
              <span>GitHub</span>
              <ArrowRight size={16} className="group-hover:translate-x-1 transition-transform" />
            </a>
            <a
              href="https://t.me/thukhasusan78"
              target="_blank"
              rel="noopener noreferrer"
              className="neon-button flex items-center justify-center gap-2 group"
            >
              <Send size={20} />
              <span>Telegram</span>
              <ArrowRight size={16} className="group-hover:translate-x-1 transition-transform" />
            </a>
          </div>

          {/* Scroll indicator */}
          <div className={`mt-16 flex justify-center transition-all duration-1000 delay-1000 ${isVisible ? 'opacity-100' : 'opacity-0'}`}>
            <div className="animate-bounce">
              <svg
                className="w-6 h-6 text-primary"
                fill="none"
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="2"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path d="M19 14l-7 7m0 0l-7-7m7 7V3"></path>
              </svg>
            </div>
          </div>
        </div>
      </div>

      {/* Gradient overlay */}
      <div className="absolute inset-0 bg-gradient-to-b from-transparent via-transparent to-background pointer-events-none" />
    </section>
  );
}
